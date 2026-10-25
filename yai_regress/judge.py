"""Optional LLM judge for a test's plain-language `expected_outcome`.

Talks to any OpenAI-compatible /chat/completions endpoint (OpenAI, OpenRouter, Ollama,
vLLM, a gateway). When no judge is configured, expected_outcome is reported as
"unjudged" and the deterministic checks decide the result on their own.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Callable

Judge = Callable[[str, str], "tuple[bool, str]"]

PROMPT = """You grade a customer-service bot conversation against a rule.
Rule (what must and must not happen):
{outcome}

Transcript:
{transcript}

Answer with JSON only: {{"pass": true|false, "reason": "<one sentence>"}}"""


def parse_verdict(text: str) -> tuple[bool, str]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"judge did not return JSON: {text[:120]!r}")
    obj = json.loads(m.group(0))
    if not isinstance(obj.get("pass"), bool):
        raise ValueError(f"judge JSON has no boolean 'pass': {obj!r}")
    return obj["pass"], str(obj.get("reason", ""))


def openai_compatible_judge(base_url: str, api_key: str, model: str, timeout: float = 60.0) -> Judge:
    url = base_url.rstrip("/") + "/chat/completions"

    def judge(outcome: str, transcript: str) -> tuple[bool, str]:
        body = {
            "model": model,
            "temperature": 0,
            "messages": [{"role": "user", "content": PROMPT.format(outcome=outcome, transcript=transcript)}],
        }
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read().decode())
        return parse_verdict(out["choices"][0]["message"]["content"])

    return judge


def judge_from_env() -> Judge | None:
    base, model = os.environ.get("JUDGE_BASE_URL"), os.environ.get("JUDGE_MODEL")
    if not (base and model):
        return None
    return openai_compatible_judge(base, os.environ.get("JUDGE_API_KEY", ""), model)
