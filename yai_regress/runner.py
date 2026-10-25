"""Run a suite against a bot and decide Passing / Failing / Systematic error per test."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol

from .client import Reply, SystematicError
from .judge import Judge
from .suite import Suite, TestCase, check_final, check_turn

PASSING, FAILING, SYSTEMATIC = "passing", "failing", "systematic_error"


class Sender(Protocol):
    def send(self, sender: str, message: str | None = None, event: dict[str, Any] | None = None,
             fetch_variables: list[str] | None = None) -> Reply: ...


@dataclass
class Iteration:
    status: str
    failures: list[str] = field(default_factory=list)
    transcript: list[dict[str, str]] = field(default_factory=list)
    judge: str = "unjudged"
    latency_ms: float = 0.0


@dataclass
class TestResult:
    name: str
    datasets: list[str]
    status: str
    flaky: bool
    pass_rate: float
    iterations: list[Iteration]

    def first_failure(self) -> str:
        for it in self.iterations:
            if it.failures:
                return it.failures[0]
        return ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_iteration(bot: Sender, tc: TestCase, judge: Judge | None) -> Iteration:
    sender = f"yai-regress-{tc.name}-{uuid.uuid4().hex[:8]}"  # fresh session per iteration
    fetch = tc.variable_names() or None
    it = Iteration(status=PASSING)
    replies: list[Reply] = []
    try:
        if tc.user_profile:
            bot.send(sender, event=tc.user_profile)
        for n, turn in enumerate(tc.conversation, 1):
            said = turn.user if turn.user is not None else f"[event {turn.event.get('code')}]"
            reply = bot.send(sender, message=turn.user, event=turn.event, fetch_variables=fetch)
            replies.append(reply)
            it.latency_ms += reply.latency_ms
            it.transcript += [{"role": "user", "text": said}, {"role": "bot", "text": reply.text}]
            for check in turn.expect:
                why = check_turn(check, reply)
                if why:
                    it.failures.append(f"turn {n}: {why}")
    except SystematicError as e:
        it.status = SYSTEMATIC
        it.failures.append(f"systematic: {e}")
        return it

    user_turns = sum(1 for t in tc.conversation if t.user is not None)
    for check in tc.assertions:
        why = check_final(check, replies, user_turns)
        if why:
            it.failures.append(f"final: {why}")

    if judge and tc.expected_outcome:
        text = "\n".join(f"{m['role'].upper()}: {m['text']}" for m in it.transcript)
        try:
            ok, reason = judge(tc.expected_outcome, text)
            it.judge = f"{'pass' if ok else 'fail'}: {reason}"
            if not ok:
                it.failures.append(f"judge: {reason}")
        except Exception as e:  # judge outage must not look like a bot failure
            it.judge = f"error: {e}"

    if it.failures:
        it.status = FAILING
    return it


def run_test(bot: Sender, tc: TestCase, judge: Judge | None = None, iterations: int | None = None) -> TestResult:
    its = [run_iteration(bot, tc, judge) for _ in range(iterations or tc.iterations)]
    passed = sum(1 for i in its if i.status == PASSING)
    failed = sum(1 for i in its if i.status == FAILING)
    if failed:
        status = FAILING
    elif any(i.status == SYSTEMATIC for i in its):
        status = SYSTEMATIC
    else:
        status = PASSING
    return TestResult(
        name=tc.name,
        datasets=tc.datasets,
        status=status,
        flaky=passed > 0 and failed > 0,
        pass_rate=round(passed / len(its), 3),
        iterations=its,
    )


def run_suite(bot: Sender, suite: Suite, judge: Judge | None = None, dataset: str | None = None,
              iterations: int | None = None,
              bot_for_agent: Callable[[str], Sender] | None = None) -> list[TestResult]:
    """`bot_for_agent` builds a sender for tests that name a different bot id in `agent`."""
    results = []
    for tc in suite.tests:
        if dataset and dataset not in tc.datasets:
            continue
        target = bot_for_agent(tc.agent) if (tc.agent and bot_for_agent) else bot
        results.append(run_test(target, tc, judge, iterations))
    return results
