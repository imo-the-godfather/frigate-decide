"""Ollama /v1/systemone client with ordered endpoint fallback and fail-open."""
import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

FAIL_OPEN = "fail_open"
SENTINEL = "__fail_open__"


@dataclass
class Decision:
    answers: dict
    latency_ms: int
    endpoint: str
    model: str
    gate: str = "decided"  # decided | fail_open
    attempts: list = field(default_factory=list)

    @property
    def is_fail_open(self) -> bool:
        return self.gate == FAIL_OPEN


class AllEndpointsFailed(Exception):
    pass


def _ask_one(endpoint: str, body: dict, timeout_s: float) -> dict:
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/systemone",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.load(resp)


def ask(image_bytes: bytes, state: dict, questions: dict, endpoints: list[str],
        model: str, timeout_s: float = 8.0, keep_alive: str = "5m") -> Decision:
    """Ask the ordered endpoints in turn. Every endpoint down/slow -> fail-open."""
    body = {
        "model": model,
        "state": state,
        "questions": questions,
        "images": [base64.b64encode(image_bytes).decode()],
        "keep_alive": keep_alive,
    }
    attempts = []
    started_all = time.time()
    for endpoint in endpoints:
        started = time.time()
        try:
            result = _ask_one(endpoint, body, timeout_s)
        except Exception as exc:  # noqa: BLE001 - any failure means try the next endpoint
            attempts.append({"endpoint": endpoint, "error": str(exc)[:200]})
            continue
        answers = result.get("answers")
        if not isinstance(answers, dict) or not answers:
            attempts.append({"endpoint": endpoint, "error": "no answers in response"})
            continue
        return Decision(
            answers=answers,
            latency_ms=round((time.time() - started) * 1000),
            endpoint=endpoint,
            model=model,
            attempts=attempts,
        )
    return Decision(
        answers={},
        latency_ms=round((time.time() - started_all) * 1000),
        endpoint=None,
        model=model,
        gate=FAIL_OPEN,
        attempts=attempts,
    )
