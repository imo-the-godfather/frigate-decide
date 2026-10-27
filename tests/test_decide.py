import pytest

from frigate_decide import decide
from tests.conftest import png_bytes

Q = {"scene": {"type": "choice", "instructions": "what is this",
               "criteria": {"a": "x", "b": "y"}}}


def test_fallback_to_second_endpoint(monkeypatch):
    calls = []

    def fake(endpoint, body, timeout_s):
        calls.append(endpoint)
        if endpoint.startswith("http://down"):
            raise OSError("connection refused")
        return {"answers": {"scene": {"choice": "b"}}}

    monkeypatch.setattr(decide, "_ask_one", fake)
    d = decide.ask(png_bytes(), {"camera": "c"}, Q,
                   ["http://down:11434", "http://up:11434"], "d1")
    assert d.endpoint == "http://up:11434"
    assert d.answers["scene"]["choice"] == "b"
    assert d.gate == "decided"
    assert len(calls) == 2 and "down" in calls[0]


def test_all_endpoints_down_fails_open(monkeypatch):
    def fake(endpoint, body, timeout_s):
        raise OSError("connection refused")

    monkeypatch.setattr(decide, "_ask_one", fake)
    d = decide.ask(png_bytes(), {}, Q, ["http://a:1", "http://b:2"], "d1", timeout_s=0.2)
    assert d.is_fail_open
    assert d.answers == {}
    assert len(d.attempts) == 2


def test_slow_endpoint_times_out_then_next_answers(monkeypatch):
    def fake(endpoint, body, timeout_s):
        if endpoint.startswith("http://slow"):
            import time
            time.sleep(0.05)
            raise TimeoutError("timed out")  # client gave up first
        return {"answers": {"scene": {"choice": "a"}}}

    monkeypatch.setattr(decide, "_ask_one", fake)
    d = decide.ask(png_bytes(), {}, Q, ["http://slow:1", "http://fast:1"], "d1")
    assert not d.is_fail_open
    assert d.endpoint == "http://fast:1"


def test_malformed_response_treated_as_failure(monkeypatch):
    monkeypatch.setattr(decide, "_ask_one",
                        lambda e, b, t: {"answers": {}})
    d = decide.ask(png_bytes(), {}, Q, ["http://x:1"], "d1")
    assert d.is_fail_open


def test_image_is_base64_encoded(monkeypatch):
    seen = {}

    def fake(endpoint, body, timeout_s):
        seen.update(body)
        import base64
        assert base64.b64decode(body["images"][0]) == png_bytes()
        return {"answers": {"scene": {"choice": "a"}}}

    monkeypatch.setattr(decide, "_ask_one", fake)
    decide.ask(png_bytes(), {"camera": "c"}, Q, ["http://x:1"], "d1")
    assert seen["model"] == "d1" and seen["state"] == {"camera": "c"}
