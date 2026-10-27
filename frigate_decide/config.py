"""Config loading + validation (YAML file or dict)."""
from pathlib import Path

import yaml

from .questions import default_questions, validate_questions

DEFAULTS = {
    "mqtt": {"host": "127.0.0.1", "port": 1883, "username": None, "password": None},
    "frigate": {
        "url": "http://127.0.0.1:5000",
        "topics": ["frigate/events", "frigate/reviews"],
    },
    "decide": {
        "endpoints": ["http://127.0.0.1:11434"],
        "model": "d1",
        "timeout_s": 8.0,
        "keep_alive": "5m",
    },
    "rules": {
        "notify_when": {"question": "scene", "field": "choice", "in": ["person_property"]},
    },
    "prefilter": {
        "labels": [],
        "min_score": 0.0,
        "stationary_only": False,
        "zones": [],
    },
    "cooldown_s": 120,
    "queue_max": 8,
    "shadow": True,
    "ha": {"webhook": None},
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def validate(cfg: dict) -> dict:
    merged = _merge(DEFAULTS, cfg)
    decide = merged["decide"]
    if not decide["endpoints"]:
        raise ValueError("decide.endpoints must list at least one Ollama URL")
    if float(decide["timeout_s"]) <= 0:
        raise ValueError("decide.timeout_s must be > 0")
    if int(merged["cooldown_s"]) < 0:
        raise ValueError("cooldown_s must be >= 0")
    if int(merged["queue_max"]) < 1:
        raise ValueError("queue_max must be >= 1")
    questions = merged.get("questions") or default_questions()
    validate_questions(questions)
    merged["questions"] = questions
    rule = merged["rules"]["notify_when"]
    if not rule.get("question") or not rule.get("field"):
        raise ValueError("rules.notify_when needs question + field")
    if rule["question"] not in merged["questions"]:
        raise ValueError(f"rules.notify_when.question {rule['question']!r} has no such question")
    if not rule.get("in"):
        raise ValueError("rules.notify_when.in must list at least one answer value")
    webhook = merged["ha"]["webhook"]
    if not merged["shadow"] and not webhook:
        raise ValueError("ha.webhook is required when shadow is false")
    return merged


def load(path: str | Path) -> dict:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return validate(raw)
