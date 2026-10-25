import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from yai_regress import report
from yai_regress.client import Reply, YellowClient
from yai_regress.cli import main
from yai_regress.judge import parse_verdict
from yai_regress.runner import FAILING, PASSING, SYSTEMATIC, run_suite, run_test
from yai_regress.suite import SuiteError, check_turn, load_suite

from .conftest import BOT, KEY

SUITE = Path(__file__).parent.parent / "suites" / "card_support.yaml"


def by_name(results):
    return {r.name: r for r in results}


def reply(text, tags=(), variables=None, ms=10.0):
    return Reply([text], list(tags), variables or {}, ms)


def test_turn_checks():
    r = reply("Tomorrow I have 10:00 or 14:00 open.", ["x"], ms=50)
    assert check_turn({"contains": "10:00"}, r) is None
    assert check_turn({"not_contains": "booked"}, r) is None
    assert "forbidden" in check_turn({"not_contains": "10:00"}, r)
    assert check_turn({"regex": r"\d\d:\d\d"}, r) is None
    assert check_turn({"any_of": ["nope", "14:00"]}, r) is None
    assert check_turn({"tag": "x"}, r) is None
    assert "latency" in check_turn({"max_latency_ms": 10}, r)
    assert "expected" in check_turn({"variable": {"name": "v", "equals": 1}}, reply("", variables={"v": 2}))


def test_suite_validation(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("tests:\n  - name: a\n    conversation:\n      - user: hi\n        expect:\n          - smells_good: yes\n")
    with pytest.raises(SuiteError, match="unknown check"):
        load_suite(bad)
    bad.write_text("tests:\n  - name: a\n    conversation: []\n")
    with pytest.raises(SuiteError, match="empty"):
        load_suite(bad)


def test_good_build_passes_everything(good_client):
    results = run_suite(good_client, load_suite(SUITE))
    assert {r.name: r.status for r in results} == {r.name: PASSING for r in results}
    assert len(results) == 6


def test_buggy_build_caught(buggy_client):
    res = by_name(run_suite(buggy_client, load_suite(SUITE)))
    assert res["refuses-unoffered-time"].status == FAILING
    assert res["refund-window"].status == FAILING
    assert "card_status" in res["blocks-lost-card"].first_failure()
    assert res["greets-with-menu"].status == FAILING and res["greets-with-menu"].flaky
    assert res["handoff-on-request"].status == PASSING


def test_dataset_filter(good_client):
    results = run_suite(good_client, load_suite(SUITE), dataset="booking")
    assert sorted(r.name for r in results) == ["books-offered-slot", "refuses-unoffered-time"]


def test_judge_used_and_judge_outage_is_not_a_failure(good_client):
    suite = load_suite(SUITE)
    tc = next(t for t in suite.tests if t.name == "refuses-unoffered-time")
    seen = []

    def strict(outcome, transcript):
        seen.append(transcript)
        return False, "booked anyway"

    assert run_test(good_client, tc, strict).status == FAILING
    assert "USER: 3 pm please" in seen[0]

    def broken(outcome, transcript):
        raise RuntimeError("judge down")

    r = run_test(good_client, tc, broken)
    assert r.status == PASSING and r.iterations[0].judge.startswith("error")


def test_parse_verdict():
    assert parse_verdict('```json\n{"pass": true, "reason": "ok"}\n```') == (True, "ok")
    with pytest.raises(ValueError):
        parse_verdict("looks fine to me")


def test_unreachable_bot_is_systematic():
    dead = YellowClient(BOT, KEY, base_url="http://127.0.0.1:9", retries=0, timeout=2)
    results = run_suite(dead, load_suite(SUITE), dataset="policy")
    assert results[0].status == SYSTEMATIC
    assert report.exit_code(results, None, "any") == 2


def test_baseline_diff_and_reports(good_client, buggy_client):
    suite = load_suite(SUITE)
    base = report.to_json(run_suite(good_client, suite), suite.name)
    now = run_suite(buggy_client, suite)
    diff = report.diff_baseline(now, {k: v["status"] for k, v in base["tests"].items()})
    assert diff["refund-window"] == "regressed" and diff["handoff-on-request"] == "same"
    assert report.exit_code(now, diff, "regressions") == 1
    xml = ET.fromstring(report.to_junit(now, suite.name))
    assert xml.get("tests") == "6" and int(xml.get("failures")) >= 4
    assert "regressed vs baseline" in report.render_table(now, diff)


def test_cli_end_to_end(good_url, buggy_url, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("YELLOW_API_KEY", KEY)
    monkeypatch.delenv("JUDGE_BASE_URL", raising=False)
    base = tmp_path / "baseline.json"
    assert main(["run", str(SUITE), "--bot-id", BOT, "--base-url", good_url, "--json", str(base)]) == 0
    junit = tmp_path / "junit.xml"
    code = main(["run", str(SUITE), "--bot-id", BOT, "--base-url", buggy_url,
                 "--baseline", str(base), "--junit", str(junit)])
    out = capsys.readouterr().out
    assert code == 1 and "regressed" in out and junit.exists()
    assert json.loads(base.read_text())["tests"]["refund-window"]["status"] == PASSING
