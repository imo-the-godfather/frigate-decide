import threading
import time

from frigate_decide.queue import Event, Gatekeeper

CFG = {"queue_max": 8, "cooldown_s": 120,
       "prefilter": {"labels": ["person", "car"], "min_score": 0.6,
                     "stationary_only": False, "zones": []}}


def ev(n, camera="cam", label="person", score=0.8, zones=None, stationary=False):
    return Event(event_id=f"e{n}", camera=camera, label=label, score=score,
                 zones=zones or [], stationary=stationary)


def test_prefilter_label():
    g = Gatekeeper(CFG, lambda e: None)
    assert g.submit(ev(1, label="person")) is True
    assert g.submit(ev(2, label="package")) is False
    assert g.dropped["prefilter"] == 1


def test_prefilter_score_zone_stationary():
    g = Gatekeeper({**CFG, "prefilter": {**CFG["prefilter"], "min_score": 0.7,
                                         "zones": ["yard"], "stationary_only": True}},
                   lambda e: None)
    assert g.submit(ev(1, score=0.65)) is False
    assert g.submit(ev(2, score=0.9, zones=["street"])) is False
    assert g.submit(ev(3, score=0.9, zones=["yard"], stationary=True)) is True


def test_cooldown_drops_same_camera_burst():
    g = Gatekeeper(CFG, lambda e: None)
    assert g.submit(ev(1)) is True
    assert all(g.submit(ev(n)) is False for n in range(2, 51))
    assert g.dropped["cooldown"] == 49
    assert g.dropped["duplicate"] == 0


def test_duplicate_ids_dropped():
    g = Gatekeeper(CFG, lambda e: None)
    g.submit(ev(1, camera="a"))
    assert g.submit(ev(1, camera="b")) is False  # same id, other camera
    assert g.dropped["duplicate"] == 1


def test_50_event_burst_never_exceeds_queue_max():
    """50 distinct cameras at once: one in-flight, bounded drop-oldest queue."""
    worker_hung = threading.Event()
    processed = []

    def slow_process(event):
        processed.append(event)
        worker_hung.wait(timeout=10)  # model call "in flight" for the whole burst

    g = Gatekeeper(CFG, slow_process)
    deadline = time.time() + 5
    while not processed and time.time() < deadline:
        time.sleep(0.01)  # let the worker pick up its first event
    for n in range(50):
        g.submit(ev(n, camera=f"cam{n}"))
    assert len(g.queue) <= CFG["queue_max"]
    accounted = g.dropped["full"] + len(g.queue) + len(processed) \
        + g.dropped["prefilter"] + g.dropped["cooldown"] + g.dropped["duplicate"]
    assert accounted == 50
    worker_hung.set()
