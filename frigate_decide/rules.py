"""Rules block: answers -> notify or not.

Default shape (config.yaml):
    rules:
      notify_when:
        question: scene
        field: choice
        in: [person_property]
`in` may be a single value or a list. Fail-open always notifies: silence must
never come from a broken model.
"""


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def evaluate(answers: dict, rules: dict, gate: str = "decided") -> bool:
    if gate == "fail_open":
        return True
    when = rules["notify_when"]
    answer = answers.get(when["question"], {})
    if not isinstance(answer, dict):
        return False
    value = answer.get(when["field"])
    return value in _as_list(when["in"])


def describe(answers: dict, rules: dict, gate: str = "decided") -> str:
    when = rules["notify_when"]
    if gate == "fail_open":
        return "fail_open: no model answered, event passes through"
    answer = answers.get(when["question"], {})
    value = answer.get(when["field"]) if isinstance(answer, dict) else None
    expected = _as_list(when["in"])
    if value in expected:
        return f"{when['question']}.{when['field']}={value!r} matches {expected}"
    return f"{when['question']}.{when['field']}={value!r} not in {expected}"
