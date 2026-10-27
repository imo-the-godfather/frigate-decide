"""Pre-filters + bounded drop-oldest queue + per-camera cooldown.

A burst of street cars must never pile up slow CPU calls: one in-flight
request, a bounded queue that drops the OLDEST item when full, and one
decision per camera per cooldown window. Obvious noise (label, score,
zone, stationary) is dropped before it ever reaches the model.
"""
import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class Event:
    event_id: str
    camera: str
    label: str
    score: float
    zones: list
    stationary: bool

    def state(self) -> dict:
        return {"camera": self.camera, "detector_label": self.label}


class Gatekeeper:
    """Accept/reject events, then hand them to a worker one at a time."""

    def __init__(self, cfg: dict, process):
        self.cfg = cfg
        self.process = process  # callable(Event) -> None, runs in the worker thread
        self.queue: deque[Event] = deque(maxlen=int(cfg["queue_max"]))
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._last_seen: dict[str, float] = {}
        self.dropped = {"cooldown": 0, "full": 0, "prefilter": 0, "duplicate": 0}
        self._seen_ids: set[str] = set()
        self._worker = threading.Thread(target=self._run, daemon=True, name="frigate-decide")
        self._worker.start()

    # -- intake ------------------------------------------------------------
    def submit(self, event: Event) -> bool:
        if not self._passes_prefilter(event):
            self.dropped["prefilter"] += 1
            return False
        with self._lock:
            if event.event_id in self._seen_ids:
                self.dropped["duplicate"] += 1
                return False
            now = time.monotonic()
            last = self._last_seen.get(event.camera)
            if last is not None and now - last < float(self.cfg["cooldown_s"]):
                self.dropped["cooldown"] += 1
                return False
            self._last_seen[event.camera] = now
            self._seen_ids.add(event.event_id)
            if len(self._seen_ids) > 4096:
                self._seen_ids.clear()
            if self.queue.maxlen == len(self.queue):
                self.queue.popleft()  # drop-oldest
                self.dropped["full"] += 1
            self.queue.append(event)
        self._wake.set()
        return True

    def _passes_prefilter(self, event: Event) -> bool:
        pf = self.cfg["prefilter"]
        if pf["labels"] and event.label not in pf["labels"]:
            return False
        if event.score < float(pf["min_score"]):
            return False
        if pf["stationary_only"] and not event.stationary:
            return False
        if pf["zones"] and not set(event.zones) & set(pf["zones"]):
            return False
        return True

    # -- worker ------------------------------------------------------------
    def _run(self):
        while True:
            with self._lock:
                event = self.queue.popleft() if self.queue else None
            if event is None:
                self._wake.wait(timeout=30)
                self._wake.clear()
                continue
            self.process(event)
