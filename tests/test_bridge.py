import time

from frigate_decide.bridge import Bridge, event_from_payload
from frigate_decide.decide import FAIL_OPEN
from tests.conftest import FRIGATE_EVENT, make_decision, png_bytes

CFG = {
    "mqtt": {"host": "fake", "port": 1883},
    "frigate": {"url": "http://frigate:5000", "topics": ["frigate/events"]},
    "decide": {"endpoints": ["http://fake:11434"], "model": "d1",
               "timeout_s": 8, "keep_alive": "5m"},
    "rules": {"notify_when": {"question": "scene", "field": "choice",
                              "in": ["person_property"]}},
    "prefilter": {"labels": [], "min_score": 0.0, "stationary_only": False,
                  "zones": []},
    "cooldown_s": 120,
    "queue_max": 8,
    "shadow": True,
    "ha": {"webhook": None},
    "questions": {"scene": {"type": "choice", "instructions": "i",
                            "criteria": {"person_property": "p", "car": "c"}}},
}


def make_bridge(**over):
    cfg = {**CFG, **over}
    cfg = {**cfg,
           "mqtt": {**CFG["mqtt"], **over.get("mqtt", {})},
           "decide": {**CFG["decide"], **over.get("decide", {})},
           "ha": {**CFG["ha"], **over.get("ha", {})}}
    webhook_calls = []

    def fake_ask(image, state, questions, endpoints, model, **kw):
        assert image == png_bytes(), "snapshot bytes must reach the model"
        assert state == {"camera": "front_cam", "detector_label": "person"}
        return cfg.pop("_decision", make_decision({"scene": {"choice": "person_property"}}))

    def fake_post(url, payload, timeout_s=5.0):
        webhook_calls.append((url, payload))
        return True

    bridge = Bridge(cfg, ask_fn=fake_ask, http_post=fake_post,
                    mqtt_factory=lambda: FakeMqtt(),
                    snapshot_fn=lambda event_id: png_bytes())
    return bridge, webhook_calls


class FakeMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))

    def username_pw_set(self, *a):
        pass

    def connect(self, *a, **kw):
        return 0

    def loop_stop(self):
        pass


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_mqtt_event_in_decision_out():
    """AC1: a Frigate MQTT event goes in, a decision comes out on MQTT."""
    bridge, _ = make_bridge()
    assert bridge.handle_payload(FRIGATE_EVENT) is True
    assert wait_for(lambda: bridge.published)
    record = bridge.published[-1]
    assert record["event_id"] == FRIGATE_EVENT["after"]["id"]
    assert record["notify"] is True
    assert record["answers"]["scene"]["choice"] == "person_property"


def test_end_message_is_ignored():
    bridge, _ = make_bridge()
    payload = {"before": {**FRIGATE_EVENT["after"]},
               "after": {**FRIGATE_EVENT["after"], "end_time": 1793046500.0}}
    assert bridge.handle_payload(payload) is False


def test_shadow_mode_decides_but_never_pages():
    """AC1: rules -> webhook called or not. Shadow=true means never."""
    bridge, webhook_calls = make_bridge(shadow=True)
    assert bridge.handle_payload(FRIGATE_EVENT) is True
    assert wait_for(lambda: bridge.published)
    assert webhook_calls == []


def test_live_mode_pages_ha_webhook_on_match():
    bridge, webhook_calls = make_bridge(
        shadow=False, ha={"webhook": "http://ha:8123/api/webhook/fd"})
    assert bridge.handle_payload(FRIGATE_EVENT) is True
    assert wait_for(lambda: webhook_calls)
    assert webhook_calls[0][0] == "http://ha:8123/api/webhook/fd"
    assert webhook_calls[0][1]["notify"] is True


def test_live_mode_no_webhook_when_rules_silence():
    bridge, webhook_calls = make_bridge(
        shadow=False, ha={"webhook": "http://ha:8123/api/webhook/fd"},
        _decision=make_decision({"scene": {"choice": "vehicle_street"}}))
    assert bridge.handle_payload(FRIGATE_EVENT) is True
    assert wait_for(lambda: bridge.published)
    assert bridge.published[-1]["notify"] is False
    time.sleep(0.2)
    assert webhook_calls == []


def test_fail_open_when_every_endpoint_down():
    """AC1: no model answers -> event passes through exactly as Frigate would."""
    bridge, webhook_calls = make_bridge(
        shadow=False, ha={"webhook": "http://ha:8123/api/webhook/fd"},
        _decision=make_decision({}, gate=FAIL_OPEN))
    assert bridge.handle_payload(FRIGATE_EVENT) is True
    assert wait_for(lambda: webhook_calls)
    record = bridge.published[-1]
    assert record["gate"] == FAIL_OPEN and record["notify"] is True


def test_snapshot_failure_skips_without_decision():
    bridge, _ = make_bridge()
    bridge.snapshot_fn = lambda event_id: None
    assert bridge.handle_payload(FRIGATE_EVENT) is True
    time.sleep(0.3)
    assert bridge.published == []


def test_reviews_payload_yields_per_detection_events():
    review = {
        "type": "new", "id": "review-1", "camera": "front_cam",
        "data": {"detections": [{"id": "det-9", "label": "car", "score": 0.9,
                                 "data": {"zones": ["driveway"]}}]},
    }
    event = event_from_payload(review)
    assert event.event_id == "det-9" and event.label == "car"
    assert event.zones == ["driveway"]


def test_garbage_payloads_are_safe():
    assert event_from_payload(None) is None
    assert event_from_payload([]) is None
    assert event_from_payload({"after": {"camera": "cam"}}) is None
    bridge, _ = make_bridge()
    assert bridge.handle_payload({"bad": 1}) is False
