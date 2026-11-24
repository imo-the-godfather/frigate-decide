"""FLEET-88 go-live prep: per-camera cooldown burst collapse (AC1), decision
counters exposed on MQTT (AC2), bounded self.published (AC3), frame-health
check (AC4). Endpoint fallback (AC5) is covered in test_decide.py.

All images are SYNTHETIC (conftest); MQTT / model / snapshot are fakes."""
import json
import logging

import pytest

import frigate_decide.queue as queue_mod
from frigate_decide.bridge import STATS_TOPIC, Bridge, frame_error
from frigate_decide.config import validate
from frigate_decide.decide import FAIL_OPEN
from frigate_decide.queue import Event, Gatekeeper
from tests.conftest import FRIGATE_EVENT, make_decision, png_bytes
from tests.test_bridge import CFG, FakeMqtt, wait_for


def event(camera="eva_lrc", event_id="ev", label="person",
          zones=None):
    payload = json.loads(json.dumps(FRIGATE_EVENT))  # deep copy
    after = payload["after"]
    after["camera"] = camera
    after["id"] = event_id
    after["label"] = label
    after["current_zones"] = ["garden_fence"] if zones is None else zones
    return payload


class FakeClock:
    """Stand-in for the time module inside frigate_decide.queue."""

    def __init__(self, t=0.0):
        self.t = t

    def monotonic(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_golive_bridge(over=None, decisions=None):
    cfg = {**CFG, "cooldown_s": {"default": 120, "eva_lrc": 2100},
           "published_cap": 200}
    cfg.update(over or {})
    calls = []
    clients = []
    queue = iter(decisions) if decisions else None

    def ask(image, state, questions, endpoints, model, **kw):
        calls.append(state["camera"])
        return next(queue) if queue is not None else \
            make_decision({"scene": {"choice": "person_property"}})

    def factory():
        client = FakeMqtt()
        clients.append(client)
        return client

    bridge = Bridge(cfg, ask_fn=ask, http_post=lambda *a, **k: True,
                    mqtt_factory=factory, snapshot_fn=lambda e: png_bytes())
    bridge.model_calls = calls
    bridge.clients = clients
    return bridge


def all_messages(bridge):
    return [(topic, payload) for client in bridge.clients
            for (topic, payload) in client.published]


# -- AC1: per-camera cooldown collapses the lrc burst ----------------------
def test_config_accepts_per_camera_cooldown():
    merged = validate({"cooldown_s": {"eva_lrc": 2100}})
    assert merged["cooldown_s"] == {"eva_lrc": 2100.0, "default": 120.0}
    assert validate({"cooldown_s": 90})["cooldown_s"] == 90


def test_config_rejects_bad_cooldown_and_cap():
    with pytest.raises(ValueError):
        validate({"cooldown_s": {"eva_lrc": -5}})
    with pytest.raises(ValueError):
        validate({"published_cap": 0})


def test_per_camera_cooldown_map_in_gatekeeper(monkeypatch):
    clock = FakeClock(0.0)
    monkeypatch.setattr(queue_mod, "time", clock)
    gate = Gatekeeper({**CFG, "cooldown_s": {"default": 120, "eva_lrc": 2100}},
                      lambda e: None)
    assert gate.submit(Event("1", "eva_lrc", "person", 0.8, [], False)) is True
    clock.advance(120)
    # 120s in, lrc's own 2100s window is still open...
    assert gate.submit(Event("2", "eva_lrc", "person", 0.8, [], False)) is False
    # ...while another camera on the default window decides freely.
    assert gate.submit(Event("3", "front_cam", "person", 0.8, [], False)) is True


def test_lrc_burst_9_events_33min_collapses(monkeypatch):
    """AC1: the observed failure shape - 9 person events over 33 minutes on
    eva_lrc (right-wall-edge ghosts) - collapses to <=2 model calls and
    <=1 would-notify on its per-camera cooldown, without touching other
    cameras' default window."""
    clock = FakeClock(0.0)
    monkeypatch.setattr(queue_mod, "time", clock)
    bridge = make_golive_bridge()

    for n in range(9):
        if n:
            clock.advance(240)  # 9 events, one every 4 min (33 min span)
        bridge.handle_payload(event(event_id=f"lrc-{n}"))
    assert wait_for(lambda: len(bridge.published) == 1)

    lrc_calls = len(bridge.model_calls)  # only ever eva_lrc so far
    assert lrc_calls <= 2, f"burst produced {lrc_calls} model calls"
    assert lrc_calls == 1
    lrc_wn = sum(1 for record in bridge.published
                 if record["camera"] == "eva_lrc" and record["notify"])
    assert lrc_wn <= 1 and lrc_wn == 1
    assert bridge.gate.dropped["cooldown"] == 8

    # a different camera inside lrc's window is NOT swallowed by it
    clock.advance(60)
    bridge.handle_payload(event(camera="front_cam", event_id="front-1"))
    assert wait_for(lambda: len(bridge.published) == 2)
    assert bridge.model_calls[-1] == "front_cam"

    # ...and front_cam itself still honours the default 120s window
    clock.advance(60)
    assert bridge.handle_payload(
        event(camera="front_cam", event_id="front-2")) is False


# -- AC2: decision counters on MQTT ----------------------------------------
def test_decision_counters_exposed_on_mqtt_stats_topic():
    """AC2: decided / would_notify / silenced / fail_open / skipped are in
    self.stats() and published to frigate_decide/stats."""
    decisions = iter([
        make_decision({"scene": {"choice": "person_property"}}),  # would notify
        make_decision({"scene": {"choice": "vehicle_street"}}),   # silenced
        make_decision({}, gate=FAIL_OPEN),                        # fail open
    ])
    bridge = make_golive_bridge(over={"cooldown_s": 0}, decisions=decisions)
    for n, cam in enumerate(("eva_lrc", "eva_orc", "eva_bbc")):
        bridge.handle_payload(event(camera=cam, event_id=f"dec-{n}"))
    bridge.handle_payload({"after": 3})  # one bad_payload skip

    assert wait_for(lambda: bridge.counts["fail_open"] == 1)
    stats = bridge.stats()
    assert stats["decided"] == 2
    assert stats["would_notify"] == 1 and stats["silenced"] == 1
    assert stats["fail_open"] == 1
    assert stats["skipped"] == 1

    stat_msgs = [json.loads(payload) for topic, payload in all_messages(bridge)
                 if topic == STATS_TOPIC]
    assert stat_msgs, "frigate_decide/stats never published"
    latest = stat_msgs[-1]
    assert latest["would_notify"] == 1 and latest["silenced"] == 1
    assert latest["fail_open"] == 1
    assert latest["skip_by_reason"]["bad_payload"] == 1


# -- AC3: self.published is bounded ----------------------------------------
def test_published_bounded_at_cap():
    """AC3: more decisions than the cap -> len(self.published) stays <= cap."""
    bridge = make_golive_bridge(over={"published_cap": 3, "cooldown_s": 0,
                                      "queue_max": 16})
    for n in range(8):
        bridge.handle_payload(event(camera=f"cam{n}", event_id=f"e{n}"))
    assert wait_for(lambda: len(bridge.model_calls) == 8)
    assert len(bridge.published) == 3


# -- AC4: frame health check ------------------------------------------------
def test_frame_error_flags_corrupt_frames():
    assert frame_error(png_bytes()) is None
    assert frame_error(png_bytes()[:20]) is not None          # truncated
    assert frame_error(b"\xff\xd8\xff\xe0" + b"\x00" * 100) is not None
    assert frame_error(b"garbage, not an image") is not None


def test_corrupt_snapshot_skips_before_the_model(caplog):
    """AC4: a glitched frame never reaches the model (FLEET-81 shadow finding:
    one visibly corrupted lrc frame produced a confident wrong answer)."""
    caplog.set_level(logging.WARNING, logger="frigate-decide")
    bridge = make_golive_bridge()
    bridge.snapshot_fn = lambda e: png_bytes()[:20]
    assert bridge.handle_payload(event(event_id="glitch-1")) is True
    assert wait_for(lambda: bridge.skip_by_reason.get("bad_frame") == 1)
    assert not bridge.model_calls
    assert len(bridge.published) == 0
    assert any("BAD_FRAME" in record.message for record in caplog.records)
