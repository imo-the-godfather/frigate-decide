"""Home Assistant webhook call. HA's own MQTT integration may point at
127.0.0.1 with no broker, so notification goes through a webhook automation
that calls notify.mobile_app_* - configured once by the user.
"""
import json
import urllib.request


def notify(webhook_url: str, payload: dict, timeout_s: float = 5.0) -> bool:
    req = urllib.request.Request(
        webhook_url,
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001 - a missed notification must not kill the worker
        return False
