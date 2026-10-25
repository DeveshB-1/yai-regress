"""Reproduce the README demo: baseline on a good build, then catch a bad build.

    uv run python demo/run_demo.py
"""

import os
import sys

from yai_regress import mockbot
from yai_regress.cli import main

BOT = "x1700000001"
os.environ["YELLOW_API_KEY"] = "test-key"
os.environ.pop("JUDGE_BASE_URL", None)

good = mockbot.serve(0, "good", bot_id=BOT)
bad = mockbot.serve(0, "buggy", bot_id=BOT)
good_url = "http://%s:%d" % good.server_address
bad_url = "http://%s:%d" % bad.server_address

runs = [
    ("release v1 (good build)", ["run", "suites/card_support.yaml", "--bot-id", BOT,
                                 "--base-url", good_url, "--json", "demo/baseline.json"]),
    ("release v2 (bad build)", ["run", "suites/card_support.yaml", "--bot-id", BOT, "--base-url", bad_url,
                                "--baseline", "demo/baseline.json", "--junit", "demo/junit.xml"]),
]
for label, argv in runs:
    print(f"## {label}\n$ yai-regress {' '.join(argv)}")
    sys.stdout.flush()
    code = main(argv)
    print(f"exit code: {code}\n")

good.shutdown()
bad.shutdown()
