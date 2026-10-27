import json

from frigate_decide import eval as evalmod
from tests.conftest import make_decision, png_bytes

TRUTH = ["person_property", "vehicle_street", "person_property"]


def build_folder(tmp_path, truth=TRUTH):
    (tmp_path / "snaps").mkdir()
    sample = []
    for i, scene in enumerate(truth):
        eid = f"000{i}-eval"
        (tmp_path / "snaps" / f"{eid}.jpg").write_bytes(png_bytes(green=i * 40))
        sample.append({"id": eid, "camera": "cam", "label": "person",
                       "score": 0.8, "zones": []})
    (tmp_path / "sample.json").write_text(json.dumps(sample))
    (tmp_path / "truth.json").write_text(
        json.dumps({"scene": truth, "notes": "synthetic"}))
    return tmp_path


def make_ask(choices):
    calls = []

    def ask(image, state, questions, endpoints, model, **kw):
        calls.append(model)
        return make_decision({"scene": {"choice": choices[len(calls) - 1]}})

    return ask, calls


def test_eval_accuracy_and_gate_table(tmp_path):
    build_folder(tmp_path)
    ask, calls = make_ask(["person_property", "vehicle_street", "vehicle_driveway"])
    lines = []
    summaries = evalmod.run(str(tmp_path), ["d1"], ["http://fake:11434"],
                            ask_fn=ask, progress=lines.append)
    assert calls == ["d1"] * 3
    s = summaries[0]
    assert s["scene_accuracy"] == "2/3"  # 2 of 3 answers match truth
    assert s["caught"] == 1 and s["missed"] == 1  # 2 truths notify, 1 answered notify
    assert s["false_pings"] == 0
    assert s["errors"] == 0
    table = evalmod.render_table(summaries)
    assert "d1" in table and "2/3" in table
    assert any("#00" in line for line in lines)


def test_eval_per_model_table(tmp_path):
    build_folder(tmp_path)
    ask, calls = make_ask(["person_property"] * 6)
    summaries = evalmod.run(str(tmp_path), ["d1", "d1-omni"], ["http://fake:1"],
                            ask_fn=ask)
    assert calls.count("d1") == 3 and calls.count("d1-omni") == 3
    assert [s["model"] for s in summaries] == ["d1", "d1-omni"]
    assert summaries[0]["caught"] == 2 and summaries[1]["caught"] == 2


def test_eval_counts_model_errors(tmp_path):
    build_folder(tmp_path)

    def ask(image, state, questions, endpoints, model, **kw):
        raise OSError("all down")

    summaries = evalmod.run(str(tmp_path), ["d1"], ["http://fake:1"], ask_fn=ask)
    assert summaries[0]["errors"] == 3
