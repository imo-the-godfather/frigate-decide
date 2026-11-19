"""MQTT bridge: Frigate event in -> model decision -> MQTT out (+ HA webhook).

Shadow mode (default) decides and publishes only; it changes nothing about
the alerts you already get. Flip `shadow: false` when you trust the gate.
"""
import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import decide, ha, rules
from .queue import Event, Gatekeeper

log = logging.getLogger("frigate-decide")

DECISION_TOPIC = "frigate_decide/{camera}/decision"


def _fmt_zones(zones) -> str:
    return ",".join(str(z) for z in (zones or [])) or "-"


def parse_event(payload: dict) -> tuple[Event | None, str, dict]:
    """Parse a payload into (event, skip_reason, context).

    event is None when the payload must be skipped; reason is then a stable
    token for the SKIP log line and the stats totals (bad_payload, ended_event,
    no_camera, no_detection_id, no_event_id), and context carries whatever
    camera/event_id/zones could be recovered for that line. Observability only:
    the accept/skip decision itself is unchanged.
    """
    if not isinstance(payload, dict):
        return None, "bad_payload", {}
    data = payload.get("after") or payload.get("before") or payload
    if not isinstance(data, dict):
        return None, "bad_payload", {}
    ctx = {
        "camera": data.get("camera"),
        "event_id": data.get("id"),
        "zones": data.get("current_zones"),
    }
    if data.get("end_time"):
        return None, "ended_event", ctx  # ended events carry no snapshot worth deciding on
    if not ctx["camera"]:
        return None, "no_camera", ctx
    detections = (data.get("data") or {}).get("detections")
    if detections:  # frigate/reviews: decide per detection, not per review
        for det in detections:
            if isinstance(det, dict) and det.get("id"):
                return _event(ctx["camera"], det.get("id"), det,
                              (det.get("data") or {}).get("zones")), "", {}
        return None, "no_detection_id", ctx
    if not ctx["event_id"]:
        return None, "no_event_id", ctx
    return _event(ctx["camera"], ctx["event_id"], data, ctx["zones"]), "", {}


def event_from_payload(payload: dict) -> Event | None:
    """Accept frigate/events ({before, after}) and frigate/reviews payloads."""
    return parse_event(payload)[0]


def _event(camera: str, event_id, data: dict, zones) -> Event:
    snap = data.get("snapshot") or {}
    score = data.get("score")
    if score is None:
        score = snap.get("score") or 0.0
    return Event(
        event_id=str(event_id),
        camera=str(camera),
        label=str(data.get("label") or ""),
        score=float(score),
        zones=list(zones or []),
        stationary=bool(data.get("stationary", False)),
    )


def fetch_snapshot(frigate_url: str, event_id: str, timeout_s: float = 10.0) -> bytes | None:
    url = f"{frigate_url.rstrip('/')}/api/events/{event_id}/snapshot.jpg?bbox=1"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            data = resp.read()
            return data if resp.status == 200 and data else None
    except Exception as exc:  # noqa: BLE001
        log.warning("snapshot fetch failed for %s: %s", event_id, exc)
        return None


class Bridge:
    def __init__(self, cfg: dict, ask_fn=None, http_post=None, mqtt_factory=None,
                 snapshot_fn=None):
        self.cfg = cfg
        self.ask_fn = ask_fn or decide.ask
        self.http_post = http_post or ha.notify
        self.snapshot_fn = snapshot_fn or (
            lambda event_id: fetch_snapshot(cfg["frigate"]["url"], event_id))
        self.gate = Gatekeeper(cfg, self._process, on_skip=self._record_skip)
        self.published: list[dict] = []
        self.counts = {"received": 0, "published": 0, "skipped": 0, "fail_open": 0}
        self.skip_by_reason: dict[str, int] = {}
        self.stats_path = cfg.get("stats_path")
        self._stats_warned = False
        if mqtt_factory:
            self.mqtt_factory = mqtt_factory
        else:
            import paho.mqtt.client as mqtt
            self.mqtt_factory = lambda: mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    # -- pipeline ----------------------------------------------------------
    def handle_payload(self, payload: dict) -> bool:
        self.counts["received"] += 1
        event, reason, ctx = parse_event(payload)
        if event is None:
            self._record_skip(reason, camera=ctx.get("camera"),
                              event_id=ctx.get("event_id"), zones=ctx.get("zones"))
            return False
        return self.gate.submit(event)

    # -- observability (FLEET-116 slice 1: skips and fail-open are never silent)
    def _record_skip(self, reason: str, event: Event | None = None, camera=None,
                     event_id=None, zones=None) -> None:
        self.counts["skipped"] += 1
        self.skip_by_reason[reason] = self.skip_by_reason.get(reason, 0) + 1
        log.info("SKIP reason=%s camera=%s event=%s zones=%s",
                 reason,
                 camera or getattr(event, "camera", None) or "-",
                 event_id or getattr(event, "event_id", None) or "-",
                 _fmt_zones(zones if zones is not None
                            else getattr(event, "zones", None)))
        self._save_stats()

    def stats(self) -> dict:
        return {
            "received": self.counts["received"],
            "published": self.counts["published"],
            "skipped": self.counts["skipped"],
            "fail_open": self.counts["fail_open"],
            "skip_by_reason": dict(self.skip_by_reason),
        }

    def _save_stats(self) -> None:
        if not self.stats_path:
            return
        try:
            stats = self.stats()
            stats["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            Path(self.stats_path).write_text(json.dumps(stats))
            self._stats_warned = False
        except Exception as exc:  # noqa: BLE001 - stats must never break the pipeline
            if not self._stats_warned:
                self._stats_warned = True
                log.warning("stats file %s not writable: %s", self.stats_path, exc)

    def _process(self, event: Event) -> None:
        image = self.snapshot_fn(event.event_id)
        if image is None:
            self._record_skip("no_snapshot", event)
            return
        decision = self.ask_fn(
            image, event.state(), self.cfg["questions"],
            self.cfg["decide"]["endpoints"], self.cfg["decide"]["model"],
            timeout_s=float(self.cfg["decide"]["timeout_s"]),
            keep_alive=self.cfg["decide"]["keep_alive"],
        )
        notify = rules.evaluate(decision.answers, self.cfg["rules"], decision.gate)
        reason = rules.describe(decision.answers, self.cfg["rules"], decision.gate)
        if decision.is_fail_open:
            self.counts["fail_open"] += 1
            log.warning(
                "FAIL_OPEN camera=%s event=%s no model answered (attempts: %s) "
                "- event passes through and notifies per the fail-open rule",
                event.camera, event.event_id,
                "; ".join(f"{a['endpoint']}: {a['error']}"
                          for a in decision.attempts) or "none recorded")
            self._save_stats()
        record = {
            "event_id": event.event_id,
            "camera": event.camera,
            "label": event.label,
            "answers": decision.answers,
            "model": decision.model,
            "endpoint": decision.endpoint,
            "latency_ms": decision.latency_ms,
            "gate": decision.gate,
            "attempts": decision.attempts,
            "notify": notify,
            "reason": reason,
            "shadow": bool(self.cfg["shadow"]),
        }
        self.publish(record)
        log.info("%s %s -> %s", event.camera, event.event_id, reason)
        if notify and not self.cfg["shadow"] and self.cfg["ha"]["webhook"]:
            self.http_post(self.cfg["ha"]["webhook"], record)

    def publish(self, record: dict) -> None:
        self.published.append(record)
        topic = DECISION_TOPIC.format(camera=record["camera"])
        try:
            client = self._client()
            client.publish(topic, json.dumps(record))
            client.loop_stop()
        except Exception as exc:  # noqa: BLE001
            log.error("decision publish failed: %s", exc)
            return
        self.counts["published"] += 1
        self._save_stats()

    # -- mqtt --------------------------------------------------------------
    def _client(self):
        client = self.mqtt_factory()
        mcfg = self.cfg["mqtt"]
        if mcfg.get("username"):
            client.username_pw_set(mcfg["username"], mcfg.get("password"))
        client.connect(mcfg["host"], int(mcfg["port"]), keepalive=30)
        return client

    def run_forever(self):
        client = self._client()
        client.on_message = self._on_message
        for topic in self.cfg["frigate"]["topics"]:
            client.subscribe(topic)
        client.loop_forever()

    def _on_message(self, _client, _userdata, msg):
        try:
            self.handle_payload(json.loads(msg.payload))
        except Exception as exc:  # noqa: BLE001
            log.error("bad message on %s: %s", msg.topic, exc)
