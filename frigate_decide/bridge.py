"""MQTT bridge: Frigate event in -> model decision -> MQTT out (+ HA webhook).

Shadow mode (default) decides and publishes only; it changes nothing about
the alerts you already get. Flip `shadow: false` when you trust the gate.
"""
import json
import logging
import urllib.request

from . import decide, ha, rules
from .queue import Event, Gatekeeper

log = logging.getLogger("frigate-decide")

DECISION_TOPIC = "frigate_decide/{camera}/decision"


def event_from_payload(payload: dict) -> Event | None:
    """Accept frigate/events ({before, after}) and frigate/reviews payloads."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("after") or payload.get("before") or payload
    if not isinstance(data, dict):
        return None
    if data.get("end_time"):
        return None  # ended events carry no snapshot worth deciding on
    camera = data.get("camera")
    if not camera:
        return None
    detections = (data.get("data") or {}).get("detections")
    if detections:  # frigate/reviews: decide per detection, not per review
        for det in detections:
            if isinstance(det, dict) and det.get("id"):
                return _event(camera, det.get("id"), det,
                              (det.get("data") or {}).get("zones"))
        return None
    if not data.get("id"):
        return None
    return _event(camera, data["id"], data, data.get("current_zones"))


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
        self.gate = Gatekeeper(cfg, self._process)
        self.published: list[dict] = []
        if mqtt_factory:
            self.mqtt_factory = mqtt_factory
        else:
            import paho.mqtt.client as mqtt
            self.mqtt_factory = lambda: mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    # -- pipeline ----------------------------------------------------------
    def handle_payload(self, payload: dict) -> bool:
        event = event_from_payload(payload)
        if event is None:
            return False
        return self.gate.submit(event)

    def _process(self, event: Event) -> None:
        image = self.snapshot_fn(event.event_id)
        if image is None:
            log.info("no snapshot for %s, skipping", event.event_id)
            return
        decision = self.ask_fn(
            image, event.state(), self.cfg["questions"],
            self.cfg["decide"]["endpoints"], self.cfg["decide"]["model"],
            timeout_s=float(self.cfg["decide"]["timeout_s"]),
            keep_alive=self.cfg["decide"]["keep_alive"],
        )
        notify = rules.evaluate(decision.answers, self.cfg["rules"], decision.gate)
        reason = rules.describe(decision.answers, self.cfg["rules"], decision.gate)
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
