# yai-regress

Run multi-turn regression suites against a Yellow.ai bot from CI, through the documented
[Send message/event to bot](https://docs.yellow.ai/api/send-message-event-to-bot) sync API.
You get a pass/fail table, a JUnit file your CI already understands, and a non-zero exit code when
a release breaks a conversation that used to work.

## Why this exists

Yellow.ai's Testing Lab (AI Trust Centre > Testing Lab) already lets a team keep test cases with a
name, a conversation, an expected outcome, assertions, a user profile and datasets. From the public
docs I couldn't find a way to run those from a deploy pipeline: no CLI, no export format, no CI hook.

So a bot change can still ship on a Friday without the suite gating it. `yai-regress` keeps the same
test-case parts in a YAML file in the repo and runs them against the bot over the public API, so
the check can sit in GitHub Actions, GitLab CI or Jenkins next to the code that changed.

## What it does

- Sends each test's turns to `POST {region}/integrations/sync/v1/message` with `x-api-key`, using a
  fresh `sender` per run so sessions never leak into each other.
- Supports the 6 documented regions (`india`, `mea`, `indonesia`, `singapore`, `usa`, `europe`) or any `--base-url`.
- Per-turn checks: `contains`, `not_contains`, `regex`, `not_regex`, `any_of`, `tag` (messageTags), `max_latency_ms`, `variable`.
- End-of-conversation checks: `variable` (via the API's `fetchVariables`, max 5 per test), `max_turns`,
  `any_reply_contains`, `no_reply_contains`.
- `user_profile` is sent first as a bot event (`{code, data}`), so a test can run as a gold-tier customer.
- `iterations: N` reruns a test with fresh sessions and flags it **flaky** if results flip.
- Statuses follow Testing Lab: `passing`, `failing`, `systematic_error` (bot down, bad key). A dead
  bot exits 2, a broken conversation exits 1, so CI can tell an outage from a regression.
- `--json` writes results that double as a baseline; `--baseline` labels each test `regressed`,
  `fixed`, `new` or `same`. `--fail-on regressions` lets a team adopt it with known failures still open.
- Optional LLM judge for the plain-language `expected_outcome`, using any OpenAI-compatible endpoint
  (`JUDGE_BASE_URL`, `JUDGE_MODEL`, `JUDGE_API_KEY`). A judge outage is recorded and never fails the test.
- `yai-regress mock` serves a local bot with the same request/response contract, so the whole thing
  runs without an API key.

## Quickstart

```bash
uv sync
uv run pytest -q                          # 18 tests
uv run python demo/run_demo.py            # the demo below, against two local mock builds

# against a real bot
export YELLOW_API_KEY=...                 # bot > Configuration > API keys
uv run yai-regress run suites/card_support.yaml --bot-id x16900024 --region india \
    --json results.json --junit junit.xml
```

A test case:

```yaml
- name: refuses-unoffered-time
  datasets: [booking, smoke]
  expected_outcome: Agent offers only 10:00 and 14:00 and does not book 15:00.
  conversation:
    - user: I want to book a branch appointment
      expect: [{contains: "10:00"}, {contains: "14:00"}]
    - user: 3 pm please
      expect: [{not_contains: booked}, {regex: "(?i)only book"}]
  assertions:
    - variable: {name: appointment_time, equals: null}
    - max_turns: 3
```

GitHub Actions step:

```yaml
- run: uv run yai-regress run suites/card_support.yaml --bot-id ${{ vars.BOT_ID }} --region india
       --baseline baseline.json --fail-on regressions --junit junit.xml
  env: { YELLOW_API_KEY: "${{ secrets.YELLOW_API_KEY }}" }
```

## Demo run

`uv run python demo/run_demo.py`, pasted as run on 2026-10-25. Release v1 is the good mock build and
becomes the baseline. Release v2 is a build that books an unoffered slot, quotes a 30-day refund
window, stops saving `card_status`, and greets inconsistently.

```
## release v1 (good build)
$ yai-regress run suites/card_support.yaml --bot-id x1700000001 --base-url http://127.0.0.1:34181 --json demo/baseline.json
TEST                    STATUS   PASS RATE  VS BASELINE  FIRST FAILURE
refuses-unoffered-time  passing  100%       -
books-offered-slot      passing  100%       -
blocks-lost-card        passing  100%       -
refund-window           passing  100%       -
greets-with-menu        passing  100%       -
handoff-on-request      passing  100%       -

6 tests: 6 passing, 0 failing (0 flaky), 0 systematic error
exit code: 0

## release v2 (bad build)
$ yai-regress run suites/card_support.yaml --bot-id x1700000001 --base-url http://127.0.0.1:45275 --baseline demo/baseline.json --junit demo/junit.xml
TEST                    STATUS           PASS RATE  VS BASELINE  FIRST FAILURE
refuses-unoffered-time  failing          0%         regressed    turn 2: reply contains forbidden 'booked'
books-offered-slot      passing          100%       same
blocks-lost-card        failing          0%         regressed    final: variable 'card_status' = None, expected 'blocked'
refund-window           failing          0%         regressed    turn 1: reply lacks '5-7 working days'
greets-with-menu        failing (flaky)  25%        regressed    turn 1: reply has none of ['card block', 'branch appointments']
handoff-on-request      passing          100%       same

6 tests: 2 passing, 4 failing (1 flaky), 0 systematic error; 4 regressed vs baseline
exit code: 1
```

The greeting test is the interesting one: it passed 1 of 4 runs. A single run in a UI could have
come up green.

## One thing I hit on the real endpoint

`https://cloud.yellow.ai/integrations/sync/v1/message` sits behind Cloudflare, which answers
**HTTP 403, `error code: 1010`** to Python's default `Python-urllib` user agent before the key is
even checked. With an explicit `User-Agent` the same call with a dummy key gets a normal **401**.
The client sets one; anyone scripting against this API with `urllib` or similar will hit the 403 and
probably blame their key.

## What's verified and what isn't

- Request shape, path, header name, region hosts and the response example come from the OpenAPI spec
  behind docs.yellow.ai (read 2026-10-25). The real India endpoint was called with a dummy key and
  returned 401, so the call path is right up to auth.
- I don't have a Yellow.ai API key, so no test has run against a real bot. All 18 tests and the demo
  use the local mock, over real HTTP.
- The docs don't show where `fetchVariables` values come back in the response. The client reads
  `data.variables` (and two near spellings) and the mock returns them there. If the real field
  differs, it's a one-line change in `client._extract_variables`.

## Layout

```
yai_regress/client.py   sync API client, regions, retries, systematic-error handling
yai_regress/suite.py    YAML suite loader + checks
yai_regress/runner.py   iterations, flaky detection, judge hook, statuses
yai_regress/report.py   table, JSON baseline, baseline diff, JUnit, exit codes
yai_regress/judge.py    optional OpenAI-compatible LLM judge
yai_regress/mockbot.py  local bot with the same API contract (good / buggy builds)
suites/card_support.yaml, tests/, demo/
```
