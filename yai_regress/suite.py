"""Suite format and assertion checks.

A test case uses the same parts as a Yellow.ai Testing Lab test (name, conversation,
expected outcome, optional assertions, user profile, agent, datasets), so a team can keep
the cases they already wrote in the product and also run them from CI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .client import MAX_FETCH_VARIABLES, Reply

TURN_CHECKS = {"contains", "not_contains", "regex", "not_regex", "any_of", "tag", "max_latency_ms", "variable"}
FINAL_CHECKS = {"variable", "max_turns", "any_reply_contains", "no_reply_contains"}


@dataclass
class Turn:
    user: str | None = None
    event: dict[str, Any] | None = None
    expect: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TestCase:
    name: str
    conversation: list[Turn]
    expected_outcome: str = ""
    assertions: list[dict[str, Any]] = field(default_factory=list)
    user_profile: dict[str, Any] | None = None  # sent first as an event: {code, data}
    agent: str | None = None                    # bot id override
    datasets: list[str] = field(default_factory=list)
    iterations: int = 1

    def variable_names(self) -> list[str]:
        names: list[str] = []
        checks = [c for t in self.conversation for c in t.expect] + self.assertions
        for c in checks:
            if "variable" in c and c["variable"]["name"] not in names:
                names.append(c["variable"]["name"])
        return names


@dataclass
class Suite:
    name: str
    tests: list[TestCase]


class SuiteError(ValueError):
    pass


def _check_keys(where: str, check: dict[str, Any], allowed: set[str]) -> None:
    if not isinstance(check, dict) or len(check) != 1:
        raise SuiteError(f"{where}: each check is a one-key mapping, got {check!r}")
    (key,) = check
    if key not in allowed:
        raise SuiteError(f"{where}: unknown check {key!r}; allowed: {sorted(allowed)}")
    if key == "variable" and not (isinstance(check[key], dict) and "name" in check[key]):
        raise SuiteError(f"{where}: variable check needs {{name, equals}}")


def load_suite(path: str | Path) -> Suite:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict) or not isinstance(raw.get("tests"), list):
        raise SuiteError(f"{path}: expected a mapping with a 'tests' list")
    defaults = raw.get("defaults") or {}
    tests: list[TestCase] = []
    seen: set[str] = set()
    for i, t in enumerate(raw["tests"]):
        name = t.get("name") or f"test-{i}"
        if name in seen:
            raise SuiteError(f"duplicate test name {name!r}")
        seen.add(name)
        turns = []
        for j, turn in enumerate(t.get("conversation") or []):
            if ("user" in turn) == ("event" in turn):
                raise SuiteError(f"{name} turn {j}: give exactly one of 'user' or 'event'")
            for c in turn.get("expect") or []:
                _check_keys(f"{name} turn {j}", c, TURN_CHECKS)
            turns.append(Turn(user=turn.get("user"), event=turn.get("event"), expect=turn.get("expect") or []))
        if not turns:
            raise SuiteError(f"{name}: conversation is empty")
        for c in t.get("assertions") or []:
            _check_keys(f"{name} assertions", c, FINAL_CHECKS)
        tc = TestCase(
            name=name,
            conversation=turns,
            expected_outcome=t.get("expected_outcome", ""),
            assertions=t.get("assertions") or [],
            user_profile=t.get("user_profile"),
            agent=t.get("agent"),
            datasets=list(t.get("datasets") or []),
            iterations=int(t.get("iterations", defaults.get("iterations", 1))),
        )
        if len(tc.variable_names()) > MAX_FETCH_VARIABLES:
            raise SuiteError(f"{name}: the sync API returns at most {MAX_FETCH_VARIABLES} variables per call")
        tests.append(tc)
    return Suite(name=raw.get("suite", Path(path).stem), tests=tests)


def check_turn(check: dict[str, Any], reply: Reply) -> str | None:
    """Return None when the check passes, else a short reason."""
    (kind, arg), = check.items()
    text = reply.text
    low = text.lower()
    if kind == "contains":
        return None if str(arg).lower() in low else f"reply lacks {arg!r}"
    if kind == "not_contains":
        return None if str(arg).lower() not in low else f"reply contains forbidden {arg!r}"
    if kind == "regex":
        return None if re.search(arg, text) else f"reply does not match /{arg}/"
    if kind == "not_regex":
        return None if not re.search(arg, text) else f"reply matches forbidden /{arg}/"
    if kind == "any_of":
        return None if any(str(a).lower() in low for a in arg) else f"reply has none of {arg!r}"
    if kind == "tag":
        return None if arg in reply.tags else f"tag {arg!r} not in {reply.tags}"
    if kind == "max_latency_ms":
        return None if reply.latency_ms <= float(arg) else f"latency {reply.latency_ms:.0f}ms > {arg}ms"
    if kind == "variable":
        return check_variable(arg, reply.variables)
    raise SuiteError(f"unknown check {kind!r}")


def check_variable(arg: dict[str, Any], variables: dict[str, Any]) -> str | None:
    name = arg["name"]
    if "equals" not in arg:
        return None if variables.get(name) is not None else f"variable {name!r} is not set"
    got = variables.get(name)
    return None if got == arg["equals"] else f"variable {name!r} = {got!r}, expected {arg['equals']!r}"


def check_final(check: dict[str, Any], replies: list[Reply], user_turns: int) -> str | None:
    (kind, arg), = check.items()
    if kind == "max_turns":
        return None if user_turns <= int(arg) else f"took {user_turns} turns, max {arg}"
    if kind == "variable":
        return check_variable(arg, replies[-1].variables if replies else {})
    all_text = "\n".join(r.text for r in replies).lower()
    if kind == "any_reply_contains":
        return None if str(arg).lower() in all_text else f"no reply contains {arg!r}"
    if kind == "no_reply_contains":
        return None if str(arg).lower() not in all_text else f"a reply contains forbidden {arg!r}"
    raise SuiteError(f"unknown check {kind!r}")
