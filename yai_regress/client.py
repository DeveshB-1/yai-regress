"""Client for Yellow.ai's "Send message/event to bot" sync API.

Contract taken from the OpenAPI spec behind https://docs.yellow.ai/api/send-message-event-to-bot
(read 2026-10-25):

    POST {server}/integrations/sync/v1/message
    header  x-api-key: <key>
    body    {"botId", "sender", "data": {"message" | "event": {"code", "data"}}, "fetchVariables": [..max 5]}
    200     {"success": true, "message": "success",
             "data": {"messageArray": [{"message", "messageTags", "_id"}], "targetLanguage", "botId", "sender"}}

The documented example response does not show where fetched variables come back.
`_extract_variables` looks in the likely places and returns {} when none are present.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

SYNC_PATH = "/integrations/sync/v1/message"

# Server list from the same OpenAPI spec.
REGIONS = {
    "india": "https://cloud.yellow.ai",
    "mea": "https://r1.cloud.yellow.ai",
    "indonesia": "https://r2.cloud.yellow.ai/cdp/api/v1",
    "singapore": "https://r3.cloud.yellow.ai",
    "usa": "https://r4.cloud.yellow.ai",
    "europe": "https://r5.cloud.yellow.ai",
}

MAX_FETCH_VARIABLES = 5
# Cloudflare in front of cloud.yellow.ai rejects the default Python-urllib agent (error 1010).
USER_AGENT = "yai-regress/0.1 (+https://github.com/DeveshB-1/yai-regress)"


class SystematicError(Exception):
    """The run could not finish (network, auth, 5xx). Kept apart from a test failure."""


@dataclass
class Reply:
    messages: list[str]
    tags: list[str]
    variables: dict[str, Any]
    latency_ms: float
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


def _message_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        msg = item.get("message")
        if isinstance(msg, str):
            return msg
        if isinstance(msg, dict):  # rich cards: keep whatever text they carry
            return json.dumps(msg, ensure_ascii=False)
    return json.dumps(item, ensure_ascii=False)


def _extract_variables(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("variables", "fetchVariables", "fetchedVariables"):
        val = data.get(key)
        if isinstance(val, dict):
            return val
    return {}


def parse_response(body: dict[str, Any], latency_ms: float = 0.0) -> Reply:
    if not body.get("success", False):
        raise SystematicError(f"API returned success=false: {body.get('message')!r}")
    data = body.get("data") or {}
    items = data.get("messageArray") or []
    tags: list[str] = []
    for it in items:
        if isinstance(it, dict):
            tags.extend(t for t in it.get("messageTags") or [] if isinstance(t, str))
    return Reply(
        messages=[_message_text(it) for it in items],
        tags=tags,
        variables=_extract_variables(data),
        latency_ms=latency_ms,
        raw=body,
    )


class YellowClient:
    def __init__(
        self,
        bot_id: str,
        api_key: str,
        base_url: str | None = None,
        region: str = "india",
        timeout: float = 30.0,
        retries: int = 1,
    ):
        if not bot_id:
            raise ValueError("bot_id is required")
        if base_url is None:
            if region not in REGIONS:
                raise ValueError(f"unknown region {region!r}; pick one of {sorted(REGIONS)} or pass base_url")
            base_url = REGIONS[region]
        self.bot_id = bot_id
        self.api_key = api_key
        self.url = base_url.rstrip("/") + SYNC_PATH
        self.timeout = timeout
        self.retries = retries

    def build_body(
        self,
        sender: str,
        message: str | None = None,
        event: dict[str, Any] | None = None,
        fetch_variables: list[str] | None = None,
    ) -> dict[str, Any]:
        if (message is None) == (event is None):
            raise ValueError("send exactly one of message or event")
        data: dict[str, Any] = {"message": message} if message is not None else {"event": event}
        body: dict[str, Any] = {"botId": self.bot_id, "sender": sender, "data": data}
        if fetch_variables:
            if len(fetch_variables) > MAX_FETCH_VARIABLES:
                raise ValueError(f"API allows at most {MAX_FETCH_VARIABLES} fetchVariables")
            body["fetchVariables"] = list(fetch_variables)
        return body

    def send(self, sender: str, message: str | None = None, event: dict[str, Any] | None = None,
             fetch_variables: list[str] | None = None) -> Reply:
        payload = json.dumps(self.build_body(sender, message, event, fetch_variables)).encode()
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                self.url, data=payload, method="POST",
                headers={"Content-Type": "application/json", "x-api-key": self.api_key,
                         "User-Agent": USER_AGENT},
            )
            start = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode() or "{}")
                return parse_response(body, (time.perf_counter() - start) * 1000)
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code < 500:  # auth / bad request: retrying will not help
                    raise SystematicError(f"HTTP {e.code} from {self.url}: {e.read()[:200]!r}") from e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
            if attempt < self.retries:
                time.sleep(0.5 * (attempt + 1))
        raise SystematicError(f"could not reach {self.url}: {last_err}")
