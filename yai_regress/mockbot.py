"""Local stand-in for a Yellow.ai bot, speaking the same sync message contract.

Lets the runner, the suite and CI wiring be exercised end to end with no API key.
`variant="buggy"` simulates a bad release: it books a slot that was never offered,
quotes the wrong refund window, drops a variable and greets inconsistently.
"""

from __future__ import annotations

import json
import random
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .client import SYNC_PATH

SLOTS = ("10:00", "14:00")


class CardBot:
    def __init__(self, variant: str = "good", seed: int = 7):
        self.variant = variant
        self.rng = random.Random(seed)
        self.sessions: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def handle(self, sender: str, data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        with self.lock:
            s = self.sessions.setdefault(sender, {"state": None, "vars": {}})
            if "event" in data:
                return self._event(s, data["event"]), s["vars"]
            return self._message(s, str(data.get("message", ""))), s["vars"]

    def _event(self, s: dict[str, Any], event: dict[str, Any]) -> list[dict[str, Any]]:
        if event.get("code") == "user-profile":
            s["vars"].update({k: v for k, v in (event.get("data") or {}).items()})
            return [_m("Profile received.")]
        return [_m("Event ignored.")]

    def _message(self, s: dict[str, Any], text: str) -> list[dict[str, Any]]:
        t = text.lower().strip()
        buggy = self.variant == "buggy"

        if s["state"] == "await_last4":
            m = re.search(r"\b(\d{4})\b", t)
            if not m:
                return [_m("Please share the last 4 digits of the card.")]
            s["state"] = None
            if not buggy:  # the buggy build forgets to save the variable
                s["vars"]["card_status"] = "blocked"
            s["vars"]["card_last4"] = m.group(1)
            return [_m(f"Done. Your card ending {m.group(1)} is blocked. A new card ships in 5 working days.",
                       ["card_blocked"])]

        if s["state"] == "await_slot":
            m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", t)
            if m:
                hour = int(m.group(1)) + (12 if m.group(3) == "pm" and int(m.group(1)) < 12 else 0)
                slot = f"{hour:02d}:{m.group(2) or '00'}"
                if slot in SLOTS or buggy:  # buggy build books anything
                    s["state"] = None
                    s["vars"]["appointment_time"] = slot
                    return [_m(f"Booked. See you at the branch at {slot} tomorrow.", ["appointment_booked"])]
            return [_m(f"I can only book {SLOTS[0]} or {SLOTS[1]} tomorrow. Which one works?")]

        if re.search(r"\b(block|lost|stolen)\b", t):
            s["state"] = "await_last4"
            return [_m("I can block it right away. What are the last 4 digits of the card?")]
        if re.search(r"appointment|branch|visit", t):
            s["state"] = "await_slot"
            return [_m(f"Tomorrow I have {SLOTS[0]} or {SLOTS[1]} open. Which one?")]
        if "refund" in t:
            days = "30 days" if buggy else "5-7 working days"
            return [_m(f"Refunds reach the source account in {days}.")]
        if re.search(r"\b(agent|human|person)\b", t):
            return [_m("Connecting you to a live agent now.", ["handoff"])]
        if re.search(r"\b(hi|hello|hey)\b", t):
            name = s["vars"].get("name")
            hello = f"Hi {name}!" if name else "Hi!"
            menu = "I can help with card block, branch appointments and refunds."
            if buggy and self.rng.random() < 0.5:  # inconsistent greeting in the bad build
                menu = "How can I help?"
            return [_m(f"{hello} {menu}")]
        return [_m("Sorry, I didn't get that. Want me to connect you to an agent?", ["fallback"])]


def _m(text: str, tags: list[str] | None = None) -> dict[str, Any]:
    return {"message": text, "messageTags": tags or [], "_id": uuid.uuid4().hex[:14]}


def make_handler(bot: CardBot, api_key: str, bot_id: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep test output clean
            pass

        def _send(self, code: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path.split("?")[0] != SYNC_PATH:
                return self._send(404, {"success": False, "message": "not found"})
            if self.headers.get("x-api-key") != api_key:
                return self._send(401, {"success": False, "message": "invalid api key"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"success": False, "message": "bad json"})
            missing = [k for k in ("botId", "sender", "data") if k not in body]
            if missing:
                return self._send(400, {"success": False, "message": f"missing {missing}"})
            if body["botId"] != bot_id:
                return self._send(404, {"success": False, "message": "bot not found"})
            fetch = body.get("fetchVariables") or []
            if len(fetch) > 5:
                return self._send(400, {"success": False, "message": "fetchVariables max 5"})
            msgs, variables = bot.handle(body["sender"], body["data"])
            data = {"messageArray": msgs, "targetLanguage": "en", "botId": bot_id, "sender": body["sender"]}
            if fetch:
                data["variables"] = {k: variables.get(k) for k in fetch}
            return self._send(200, {"success": True, "message": "success", "data": data})

    return Handler


def serve(port: int = 0, variant: str = "good", api_key: str = "test-key", bot_id: str = "x1700000001",
          seed: int = 7) -> ThreadingHTTPServer:
    """Start the mock in a background thread; returns the server (call .shutdown() to stop)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(CardBot(variant, seed), api_key, bot_id))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
