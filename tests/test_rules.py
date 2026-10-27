from frigate_decide.rules import describe, evaluate

RULES = {"notify_when": {"question": "scene", "field": "choice",
                         "in": ["person_property"]}}


def ans(choice):
    return {"scene": {"choice": choice, "probabilities": {choice: 0.9}},
            "person": {"noul": "yes" if "person" in choice else "no"}}


def test_notify_on_person_property():
    assert evaluate(ans("person_property"), RULES) is True


def test_silence_street_traffic():
    for choice in ("vehicle_street", "person_street", "vehicle_driveway", "animal", "nothing"):
        assert evaluate(ans(choice), RULES) is False


def test_unknown_question_shape_is_silence_not_crash():
    assert evaluate({"unexpected": "shape"}, RULES) is False
    assert evaluate({}, RULES) is False


def test_fail_open_always_notifies():
    assert evaluate({}, RULES, gate="fail_open") is True


def test_single_value_rule():
    rules = {"notify_when": {"question": "scene", "field": "choice",
                             "in": "person_property"}}
    assert evaluate(ans("person_property"), rules) is True


def test_describe_explains_both_branches():
    assert "matches" in describe(ans("person_property"), RULES)
    assert "not in" in describe(ans("vehicle_street"), RULES)
    assert "fail_open" in describe({}, RULES, gate="fail_open")
