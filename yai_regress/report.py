"""Console table, JSON results (doubles as a baseline), JUnit XML, baseline diff."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .runner import FAILING, PASSING, SYSTEMATIC, TestResult


def to_json(results: list[TestResult], suite: str) -> dict[str, Any]:
    return {"suite": suite, "tests": {r.name: r.to_dict() for r in results}}


def load_baseline(path: str | Path) -> dict[str, str]:
    data = json.loads(Path(path).read_text())
    return {name: t["status"] for name, t in data.get("tests", {}).items()}


def diff_baseline(results: list[TestResult], baseline: dict[str, str]) -> dict[str, str]:
    """Label each test: regressed / fixed / new / same."""
    out = {}
    for r in results:
        before = baseline.get(r.name)
        if before is None:
            out[r.name] = "new"
        elif before == PASSING and r.status == FAILING:
            out[r.name] = "regressed"
        elif before == FAILING and r.status == PASSING:
            out[r.name] = "fixed"
        else:
            out[r.name] = "same"
    return out


def render_table(results: list[TestResult], diff: dict[str, str] | None = None) -> str:
    rows = [("TEST", "STATUS", "PASS RATE", "VS BASELINE", "FIRST FAILURE")]
    for r in results:
        status = r.status + (" (flaky)" if r.flaky else "")
        rows.append((r.name, status, f"{r.pass_rate:.0%}", (diff or {}).get(r.name, "-"), r.first_failure()[:70]))
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    lines = ["  ".join(c.ljust(widths[i]) if i < 4 else c for i, c in enumerate(row)).rstrip() for row in rows]
    counts = {s: sum(1 for r in results if r.status == s) for s in (PASSING, FAILING, SYSTEMATIC)}
    flaky = sum(1 for r in results if r.flaky)
    summary = (f"{len(results)} tests: {counts[PASSING]} passing, {counts[FAILING]} failing "
               f"({flaky} flaky), {counts[SYSTEMATIC]} systematic error")
    if diff:
        summary += f"; {sum(1 for v in diff.values() if v == 'regressed')} regressed vs baseline"
    return "\n".join(lines + ["", summary])


def to_junit(results: list[TestResult], suite: str) -> str:
    fails = sum(1 for r in results if r.status == FAILING)
    errs = sum(1 for r in results if r.status == SYSTEMATIC)
    ts = ET.Element("testsuite", name=suite, tests=str(len(results)), failures=str(fails), errors=str(errs))
    for r in results:
        secs = sum(i.latency_ms for i in r.iterations) / 1000
        case = ET.SubElement(ts, "testcase", classname=suite, name=r.name, time=f"{secs:.3f}")
        detail = "\n".join(f for i in r.iterations for f in i.failures)
        if r.status == FAILING:
            ET.SubElement(case, "failure", message=r.first_failure()).text = detail
        elif r.status == SYSTEMATIC:
            ET.SubElement(case, "error", message=r.first_failure()).text = detail
    return ET.tostring(ts, encoding="unicode")


def exit_code(results: list[TestResult], diff: dict[str, str] | None, fail_on: str) -> int:
    """0 ok, 1 test failures, 2 only systematic errors (bot unreachable, bad key)."""
    if fail_on == "regressions" and diff is not None:
        if any(v == "regressed" for v in diff.values()):
            return 1
    elif any(r.status == FAILING for r in results):
        return 1
    if any(r.status == SYSTEMATIC for r in results):
        return 2
    return 0
