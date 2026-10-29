"""`frigate-decide eval <folder>` - the bake-off harness, generalised.

Folder layout (any subset of questions may carry truth):
    folder/sample.json   [{"id", "camera", "label", "score", "zones"}, ...]
    folder/truth.json    {"scene": [...per-image label...], "notes": "..."}
    folder/snaps/<id>.jpg

Prints, per model: accuracy, gate confusion (caught / missed / false pings),
median + p95 latency. Gate = notify iff the rules' notify_when answer matches.
"""
import json
import statistics
import time
from pathlib import Path

from . import decide, rules
from .questions import default_questions


def load_folder(folder: str | Path) -> tuple[list[dict], dict]:
    folder = Path(folder)
    sample = json.loads((folder / "sample.json").read_text())
    truth = json.loads((folder / "truth.json").read_text())
    truth.pop("notes", None)
    return sample, truth


def read_snap(folder: Path, event_id: str) -> bytes:
    snaps = folder / "snaps"
    for ext in (".jpg", ".jpeg", ".png"):
        path = snaps / f"{event_id}{ext}"
        if path.exists():
            return path.read_bytes()
    raise FileNotFoundError(f"{event_id}: no snap in {snaps}")


def run_model(sample: list[dict], truth: dict, folder: Path, model: str,
              endpoints: list[str], questions: dict, rules_cfg: dict,
              timeout_s: float, keep_alive: str = "5m", progress=print,
              ask_fn=None) -> dict:
    ask = ask_fn or decide.ask
    rows = []
    for i, entry in enumerate(sample):
        image = read_snap(folder, entry["id"])
        state = {"camera": entry.get("camera", "eval"),
                 "detector_label": entry.get("label", "")}
        try:
            decision = ask(image, state, questions, endpoints, model,
                           timeout_s=timeout_s, keep_alive=keep_alive)
        except Exception as exc:  # noqa: BLE001
            rows.append({"i": i, "error": str(exc)[:200]})
            progress(f"#{i:02d} ERROR {exc}")
            continue
        notify = rules.evaluate(decision.answers, rules_cfg, decision.gate)
        row = {"i": i, "notify": notify, "ms": decision.latency_ms,
               "gate": decision.gate, "answers": decision.answers}
        for question, expected in truth.items():
            answer = decision.answers.get(question) or {}
            row[question] = {"truth": expected[i],
                             "got": answer.get("choice", answer.get("noul"))}
        rows.append(row)
        detail = row.get("scene", {}).get("got") if "scene" in row else ""
        progress(f"#{i:02d} {decision.latency_ms:6d}ms got={detail} "
                 f"{'NOTIFY' if notify else 'quiet'} {decision.gate}")
    return summarise(rows, truth, model, rules_cfg)


def summarise(rows: list[dict], truth: dict, model: str, rules_cfg: dict) -> dict:
    ok_rows = [r for r in rows if "error" not in r]
    summary: dict = {"model": model, "images": len(rows),
                     "errors": len(rows) - len(ok_rows)}
    for question in truth:
        got = [r[question]["got"] == r[question]["truth"]
               for r in ok_rows if question in r]
        summary[f"{question}_accuracy"] = f"{sum(got)}/{len(got)}"
    if ok_rows:
        lat = sorted(r["ms"] for r in ok_rows)
        summary["median_ms"] = int(statistics.median(lat))
        summary["p95_ms"] = lat[max(0, int(len(lat) * 0.95) - 1)]
        summary["fail_open"] = sum(1 for r in ok_rows if r["gate"] == "fail_open")
    if "scene" in truth:
        when = rules_cfg["notify_when"]
        target = set(when["in"] if isinstance(when["in"], list) else [when["in"]])
        positives = [r for r in ok_rows if r["scene"]["truth"] in target]
        negatives = [r for r in ok_rows if r["scene"]["truth"] not in target]
        summary["caught"] = sum(1 for r in positives if r["notify"])
        summary["missed"] = len(positives) - summary["caught"]
        summary["false_pings"] = sum(1 for r in negatives if r["notify"])
    return summary


def render_table(summaries: list[dict]) -> str:
    header = (f"{'model':<24}{'scene':>8}{'caught':>8}{'missed':>8}"
              f"{'false':>7}{'fail-open':>11}{'median':>9}{'p95':>7}{'errors':>8}")
    lines = [header, "-" * len(header)]
    for s in summaries:
        lines.append(
            f"{s['model']:<24}{s.get('scene_accuracy', '-')!s:>8}"
            f"{s.get('caught', '-')!s:>8}{s.get('missed', '-')!s:>8}"
            f"{s.get('false_pings', '-')!s:>7}{s.get('fail_open', '-')!s:>11}"
            f"{s.get('median_ms', '-')!s:>9}{s.get('p95_ms', '-')!s:>7}"
            f"{s['errors']!s:>8}")
    return "\n".join(lines)


def run(folder: str, models: list[str], endpoints: list[str], questions=None,
        rules_cfg=None, ask_fn=None, timeout_s: float = 8.0,
        keep_alive: str = "5m", progress=print) -> list[dict]:
    sample, truth = load_folder(folder)
    fpath = Path(folder)
    questions = questions or default_questions()
    rules_cfg = rules_cfg or {"notify_when": {"question": "scene",
                                              "field": "choice",
                                              "in": ["person_property"]}}
    ask = ask_fn or decide.ask
    progress(f"{len(sample)} images, truth for: {', '.join(truth)}")
    summaries = []
    for model in models:
        progress(f"\n== {model} ==")
        summaries.append(run_model(sample, truth, fpath, model, endpoints,
                                   questions, rules_cfg, timeout_s,
                                   keep_alive, progress, ask_fn=ask))
    progress("\n" + render_table(summaries))
    return summaries
