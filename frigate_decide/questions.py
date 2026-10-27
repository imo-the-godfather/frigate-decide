"""Typed decision questions, shared by the live gate and `frigate-decide eval`.

A question asks the model for a *decision*, not a caption:
  choice - pick exactly one criterion label
  noul   - yes / no
  score  - integer 0..N over named criteria
Answers come back with probabilities, e.g. {"scene": {"choice": "person_property",
"probabilities": {...}}, "person": {"noul": "yes", "probability": 0.97}}.
"""

QUESTION_TYPES = ("choice", "noul", "score")


def scene_question() -> dict:
    return {
        "type": "choice",
        "instructions": (
            "What is the main real subject in this home security camera snapshot? "
            "The coloured box is the detector's guess and may be wrong."
        ),
        "criteria": {
            "vehicle_street": "A vehicle driving or parked on the public street outside the fence/gate",
            "vehicle_driveway": "A vehicle inside the property, in the driveway or parking area",
            "person_street": "A person on the public street or pavement outside the fence",
            "person_property": "A person inside the property, in the garden or yard, or at the gate keypad",
            "animal": "A cat, dog or other animal",
            "nothing": (
                "No real person, vehicle or animal; the detection is a shadow, "
                "reflection, plant or other false alarm"
            ),
        },
    }


def person_question() -> dict:
    return {
        "type": "noul",
        "instructions": "Is a real human being clearly visible in the image?",
    }


def default_questions() -> dict:
    """The bake-off default set: gate on the scene choice, keep person as a check.

    The 0-2 "alert" score question from the bake-off is deliberately not in the
    default set - it was poorly calibrated; gate on the choice answer instead.
    """
    return {"scene": scene_question(), "person": person_question()}


def validate_questions(questions: dict) -> None:
    if not questions:
        raise ValueError("questions must not be empty")
    for name, q in questions.items():
        if not isinstance(name, str) or not name:
            raise ValueError("question names must be non-empty strings")
        qtype = q.get("type")
        if qtype not in QUESTION_TYPES:
            raise ValueError(f"question {name!r}: type must be one of {QUESTION_TYPES}")
        if qtype in ("choice", "score") and not q.get("criteria"):
            raise ValueError(f"question {name!r}: type {qtype!r} needs criteria")
        if not q.get("instructions"):
            raise ValueError(f"question {name!r}: instructions required")
