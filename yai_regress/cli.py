"""yai-regress command line.

    yai-regress run SUITE --bot-id ID [--region india | --base-url URL] [--iterations N]
                          [--dataset NAME] [--baseline FILE] [--fail-on any|regressions]
                          [--json OUT] [--junit OUT]
    yai-regress mock [--port 8765] [--variant good|buggy]

API key comes from YELLOW_API_KEY. The LLM judge is enabled by JUDGE_BASE_URL + JUDGE_MODEL
(+ JUDGE_API_KEY) and is optional.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import mockbot, report
from .client import REGIONS, YellowClient
from .judge import judge_from_env
from .runner import run_suite
from .suite import SuiteError, load_suite


def cmd_run(a: argparse.Namespace) -> int:
    key = os.environ.get("YELLOW_API_KEY")
    if not key:
        print("YELLOW_API_KEY is not set", file=sys.stderr)
        return 2
    try:
        suite = load_suite(a.suite)
    except SuiteError as e:
        print(f"suite error: {e}", file=sys.stderr)
        return 2

    def client(bot_id: str) -> YellowClient:
        return YellowClient(bot_id, key, base_url=a.base_url, region=a.region, timeout=a.timeout)

    results = run_suite(client(a.bot_id), suite, judge=judge_from_env(), dataset=a.dataset,
                        iterations=a.iterations, bot_for_agent=client)
    diff = report.diff_baseline(results, report.load_baseline(a.baseline)) if a.baseline else None
    print(report.render_table(results, diff))
    if a.json:
        Path(a.json).write_text(json.dumps(report.to_json(results, suite.name), indent=2))
    if a.junit:
        Path(a.junit).write_text(report.to_junit(results, suite.name))
    return report.exit_code(results, diff, a.fail_on)


def cmd_mock(a: argparse.Namespace) -> int:
    server = mockbot.serve(a.port, a.variant, api_key=a.api_key, bot_id=a.bot_id)
    host, port = server.server_address
    print(f"mock Yellow.ai bot ({a.variant}) on http://{host}:{port}  bot={a.bot_id}  x-api-key={a.api_key}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="yai-regress", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a suite against a bot")
    r.add_argument("suite")
    r.add_argument("--bot-id", required=True)
    r.add_argument("--region", default="india", choices=sorted(REGIONS))
    r.add_argument("--base-url", help="overrides --region (e.g. a mock or staging host)")
    r.add_argument("--iterations", type=int, help="override per-test iterations")
    r.add_argument("--dataset", help="only run tests in this dataset")
    r.add_argument("--baseline", help="results JSON from an earlier run")
    r.add_argument("--fail-on", choices=["any", "regressions"], default="any")
    r.add_argument("--json", help="write results JSON (reusable as --baseline)")
    r.add_argument("--junit", help="write JUnit XML for CI")
    r.add_argument("--timeout", type=float, default=30.0)
    r.set_defaults(fn=cmd_run)

    m = sub.add_parser("mock", help="serve a local mock bot with the same API contract")
    m.add_argument("--port", type=int, default=8765)
    m.add_argument("--variant", choices=["good", "buggy"], default="good")
    m.add_argument("--api-key", default="test-key")
    m.add_argument("--bot-id", default="x1700000001")
    m.set_defaults(fn=cmd_mock)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
