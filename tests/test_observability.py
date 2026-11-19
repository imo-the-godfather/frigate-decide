"""FLEET-116 slice 1: every skip and fail-open decision is visible.

Observability only - no publish/skip decision logic is exercised differently
here than in the behavior tests; these assert the LOG LINES and COUNTERS.
"""
import json
import logging

from frigate_decide.bridge import Bridge, parse_event
from frigate_decide.cli import main as cli_main
from frigate_decide.decide import FAIL_OPEN, Decision
from frigate_decide.queue import Event, Gatekeeper
from tests.conftest import FRIGATE_EVENT, make_decision
from tests.test_bridge import CFG, make_bridge, wait_for


def test_parse_skip_reasons_are_stable_tokens():
    ended = {"after": {**FRIGATE_EVENT["after"], "end_time": 1793046500.0}}
    event, reason, ctx = parse_event(ended)
    assert event is None and reason == "ended_event"
    assert ctx["camera"] == "front_cam" and ctx["zones"] == ["yard"]

    assert parse_event({"after": {"id": "x"}})[1] == "no_camera"
    assert parse_event({"after": {"camera": "cam"}})[1] == "no_event_id"
    assert parse_event({"after": {"camera": "cam", "id": "x",
                                  "data": {"detections": [{"label": "car"}]}}})[1] \
        == "no_detection_id"
    assert parse_event("not a dict")[1] == "bad_payload"
    assert parse_event({"after": 3})[1] == "bad_payload"


def test_skip_lines_carry_camera_zone_reason(caplog):
    """AC1: every skip logs one structured line; >=2 different reasons appear."""
    caplog.set_level(logging.INFO, logger="frigate-decide")
    bridge, _ = make_bridge()

    assert bridge.handle_payload(FRIGATE_EVENT) is True
    assert wait_for(lambda: bridge.counts["published"] == 1)

    ended = {"after": {**FRIGATE_EVENT["after"], "end_time": 1793046500.0}}
    assert bridge.handle_payload(ended) is False                    # ended_event
    assert bridge.handle_payload({"after": {"id": "y"}}) is False   # no_camera
    assert bridge.handle_payload({"after": {"camera": "front_cam"}}) is False
    assert bridge.handle_payload(FRIGATE_EVENT) is False            # duplicate id
    assert bridge.handle_payload(
        {"after": {**FRIGATE_EVENT["after"], "id": "fresh-id"}}) is False  # cooldown

    text = "\n".join(r.message for r in caplog.records)
    for token in ("SKIP reason=ended_event camera=front_cam",
                  "SKIP reason=no_camera camera=-",
                  "SKIP reason=no_event_id camera=front_cam",
                  "SKIP reason=duplicate camera=front_cam",
                  "SKIP reason=cooldown camera=front_cam"):
        assert token in text, f"missing log line: {token}"
    assert "zones=yard" in text  # ended_event carries the zone

    # counters reconcile with what was fed
    assert bridge.counts["received"] == 6
    assert bridge.counts["skipped"] == 5
    assert bridge.counts["published"] == 1
    assert bridge.skip_by_reason == {"ended_event": 1, "no_camera": 1,
                                     "no_event_id": 1, "duplicate": 1,
                                     "cooldown": 1}


def test_fail_open_logs_and_clip_proceeds(caplog):
    """AC2: forced model failure -> explicit FAIL_OPEN line, decision still
    published and notifies (never a silent drop)."""
    caplog.set_level(logging.WARNING, logger="frigate-decide")
    decision = Decision(answers={}, latency_ms=10, endpoint=None, model="d1",
                        gate=FAIL_OPEN,
                        attempts=[{"endpoint": "http://fake:11434",
                                   "error": "URLError: endpoint down"}])
    bridge, webhook_calls = make_bridge(
        shadow=False, ha={"webhook": "http://ha:8123/api/webhook/fd"},
        _decision=decision)

    assert bridge.handle_payload(FRIGATE_EVENT) is True
    assert wait_for(lambda: webhook_calls)

    fail_lines = [r for r in caplog.records
                  if r.levelno == logging.WARNING and "FAIL_OPEN" in r.message]
    assert len(fail_lines) == 1
    assert "camera=front_cam" in fail_lines[0].message
    assert "http://fake:11434: URLError: endpoint down" in fail_lines[0].message

    # the clip proceeded per the fail-open design: published + notified
    record = bridge.published[-1]
    assert record["gate"] == FAIL_OPEN and record["notify"] is True
    assert bridge.counts["fail_open"] == 1
    assert webhook_calls[0][0] == "http://ha:8123/api/webhook/fd"


def test_stats_file_written_and_reconciles(tmp_path):
    stats_path = tmp_path / "stats.json"
    bridge, _ = make_bridge(stats_path=str(stats_path))
    assert bridge.handle_payload(FRIGATE_EVENT) is True
    assert wait_for(lambda: bridge.counts["published"] == 1)
    assert bridge.handle_payload({"after": {"camera": "front_cam"}}) is False

    data = json.loads(stats_path.read_text())
    assert data["published"] == 1
    assert data["skipped"] == 1 and data["skip_by_reason"] == {"no_event_id": 1}
    assert data["fail_open"] == 0
    assert data["received"] == 2
    assert data["updated"].startswith("20")  # UTC ISO timestamp present


def test_unwritable_stats_file_never_breaks_pipeline(caplog):
    caplog.set_level(logging.WARNING, logger="frigate-decide")
    bridge, _ = make_bridge(stats_path="/proc/no/such/dir/stats.json")
    assert bridge.handle_payload(FRIGATE_EVENT) is True
    assert wait_for(lambda: bridge.counts["published"] == 1)
    assert any("not writable" in r.message for r in caplog.records)


def test_stats_cli_prints_totals(capsys, tmp_path):
    stats_path = tmp_path / "stats.json"
    stats_path.write_text(json.dumps({
        "received": 12, "published": 3, "skipped": 8, "fail_open": 1,
        "skip_by_reason": {"cooldown": 6, "prefilter": 2},
        "updated": "2026-11-19T05:21:00+00:00"}))
    rc = cli_main(["--config", "/nonexistent", "stats",
                   "--stats-path", str(stats_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "published: 3" in out
    assert "skipped:   8 (cooldown=6, prefilter=2)" in out
    assert "fail_open: 1" in out

    missing = cli_main(["--config", "/nonexistent", "stats",
                        "--stats-path", str(tmp_path / "nope.json")])
    assert missing == 0
    assert "no stats file at" in capsys.readouterr().out


def test_gatekeeper_reports_skip_reasons():
    seen: list[tuple[str, Event]] = []
    gcfg = {**CFG, "prefilter": {**CFG["prefilter"], "labels": ["person", "car"]}}
    g = Gatekeeper(gcfg, lambda e: None, on_skip=lambda reason, ev: seen.append((reason, ev)))
    assert g.submit(Event("1", "cam", "person", 0.8, [], False)) is True
    assert g.submit(Event("2", "cam", "package", 0.8, [], False)) is False
    assert g.submit(Event("1", "other", "person", 0.8, [], False)) is False
    assert g.submit(Event("3", "cam", "person", 0.8, [], False)) is False
    assert [reason for reason, _ in seen] == ["prefilter", "duplicate", "cooldown"]
    assert [ev.event_id for _, ev in seen] == ["2", "1", "3"]
