import io
import json

from chip_relay import lumo_route as lr

ON = {"CHIP_LUMO_SIMPLE_ROUTE": "1", "CHIP_LUMO_TAMER_URL": "http://lumo-tamer:3003/v1/chat/completions"}


def test_default_off():
    assert not lr.should_route("what is plex?", {})
    assert not lr.should_route("what is plex?", {"CHIP_LUMO_SIMPLE_ROUTE": "1"})


def test_simple_question_routes_when_flagged():
    assert lr.should_route("What port does plex use?", dict(ON))


def test_go_and_actions_never_route():
    env = dict(ON)
    assert not lr.should_route("GO: RAZ-1 fp=abc lane=x exp=2026-01-01T00:00:00Z?", env)
    assert not lr.should_route("can you deploy chip-relay?", env)
    assert not lr.should_route("should I restart qbittorrent?", env)
    assert not lr.should_route("can you delete old torrents?", env)
    assert not lr.should_route("plex is down", env)
    assert not lr.should_route("what is plex?\nand sonarr?", env)


def test_stand_down_wins():
    env = dict(ON, CHIP_STAND_DOWN="1")
    assert not lr.should_route("what is plex?", env)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ask_lumo_parses_and_fails_closed():
    ok = lambda req, timeout: _Resp(json.dumps({"choices": [{"message": {"content": " 32400 "}}]}).encode())
    assert lr.ask_lumo("q?", dict(ON), opener=ok) == "32400"

    def boom(req, timeout):
        raise OSError("down")

    assert lr.ask_lumo("q?", dict(ON), opener=boom) is None
    assert lr.ask_lumo("q?", {}, opener=ok) is None
