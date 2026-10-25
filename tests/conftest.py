import pytest

from yai_regress import mockbot
from yai_regress.client import YellowClient

KEY, BOT = "test-key", "x1700000001"


def _start(variant):
    server = mockbot.serve(0, variant, api_key=KEY, bot_id=BOT, seed=7)
    host, port = server.server_address
    return server, f"http://{host}:{port}"


@pytest.fixture
def good_url():
    server, url = _start("good")
    yield url
    server.shutdown()


@pytest.fixture
def buggy_url():
    server, url = _start("buggy")
    yield url
    server.shutdown()


@pytest.fixture
def good_client(good_url):
    return YellowClient(BOT, KEY, base_url=good_url, retries=0)


@pytest.fixture
def buggy_client(buggy_url):
    return YellowClient(BOT, KEY, base_url=buggy_url, retries=0)
