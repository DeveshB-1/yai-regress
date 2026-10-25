import pytest

from yai_regress.client import REGIONS, SYNC_PATH, SystematicError, YellowClient, parse_response

from .conftest import BOT, KEY

DOC_EXAMPLE = {  # response example from the docs.yellow.ai OpenAPI spec
    "success": True, "message": "success",
    "data": {"messageArray": [{"message": "Thank you", "messageTags": [], "_id": "65d4708e2274b2"}],
             "targetLanguage": "en", "botId": "x16900024", "sender": "896762521951619"},
}


def test_parses_documented_example():
    r = parse_response(DOC_EXAMPLE)
    assert r.messages == ["Thank you"] and r.tags == [] and r.variables == {}


def test_success_false_is_systematic():
    with pytest.raises(SystematicError):
        parse_response({"success": False, "message": "bot inactive"})


def test_region_url_and_body_shape():
    c = YellowClient("x1", "k", region="usa")
    assert c.url == REGIONS["usa"] + SYNC_PATH
    assert c.build_body("u1", message="hi", fetch_variables=["a"]) == {
        "botId": "x1", "sender": "u1", "data": {"message": "hi"}, "fetchVariables": ["a"]}
    assert c.build_body("u1", event={"code": "c", "data": {}})["data"] == {"event": {"code": "c", "data": {}}}


def test_body_validation():
    c = YellowClient("x1", "k")
    with pytest.raises(ValueError):
        c.build_body("u", message="a", event={"code": "x"})
    with pytest.raises(ValueError):
        c.build_body("u", message="a", fetch_variables=list("abcdef"))
    with pytest.raises(ValueError):
        YellowClient("x1", "k", region="mars")


def test_real_http_roundtrip_against_mock(good_client):
    r = good_client.send("s1", message="I lost my card", fetch_variables=["card_status"])
    assert "last 4 digits" in r.text
    r = good_client.send("s1", message="4821", fetch_variables=["card_status"])
    assert r.tags == ["card_blocked"] and r.variables == {"card_status": "blocked"}
    assert r.latency_ms > 0


def test_bad_key_is_systematic_not_failure(good_url):
    with pytest.raises(SystematicError, match="401"):
        YellowClient(BOT, "wrong", base_url=good_url, retries=0).send("s", message="hi")


def test_unreachable_is_systematic():
    with pytest.raises(SystematicError, match="could not reach"):
        YellowClient(BOT, KEY, base_url="http://127.0.0.1:9", retries=0, timeout=2).send("s", message="hi")


def test_sends_key_and_custom_user_agent(monkeypatch):
    # cloud.yellow.ai's Cloudflare returns 403 / error 1010 for the default Python-urllib agent
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            import json
            return json.dumps(DOC_EXAMPLE).encode()

    def fake_urlopen(req, timeout):
        seen.update({k.lower(): v for k, v in req.header_items()})
        seen["url"] = req.full_url
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    YellowClient("x1", "secret", region="europe").send("u", message="hi")
    assert seen["x-api-key"] == "secret"
    assert seen["user-agent"].startswith("yai-regress/")
    assert seen["url"] == "https://r5.cloud.yellow.ai/integrations/sync/v1/message"
