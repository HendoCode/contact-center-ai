"""
Derive the change-analysis eval sets from the specs and the generator's ground truth.

    python -m evals.datasets.build_change                     # rewrite change_golden.jsonl
    python -m evals.datasets.build_change --dataset holdout   # rewrite change_holdout.jsonl
    python -m evals.datasets.build_change --check             # exit 1 if a file is stale

Only the questions and their contracts are written by hand (change_specs.py and
change_holdout_specs.py). The change point, the reference and its derivation come from
`data/synthetic/ground_truth/events.json` and `change_log.json`, which the seed-42
generator writes (`make seed`). A golden spec about a held-out event, or a held-out spec
about a golden one, is refused.

The rows name events by their change-log name and describe golden drivers in words; no
answer-key id, and nothing a held-out driver says, is written. The evaluators match drivers
to planted calls by call id at scoring time, from the ground truth.
"""

import argparse
import json
from pathlib import Path

from evals import change_corpus
from evals.datasets.change_holdout_specs import HOLDOUT_SPECS
from evals.datasets.change_specs import BASE_EVALUATORS, CHANGE_SPECS

DATASETS_DIR = Path(__file__).resolve().parent
CHANGE_GOLDEN_PATH = DATASETS_DIR / "change_golden.jsonl"
CHANGE_HOLDOUT_PATH = DATASETS_DIR / "change_holdout.jsonl"
SETS = {"golden": (CHANGE_SPECS, CHANGE_GOLDEN_PATH), "holdout": (HOLDOUT_SPECS, CHANGE_HOLDOUT_PATH)}
EXPECTED_SOURCE = {"logged": "argument", "period": "argument", "detected": "detected"}
METRIC_LABELS = {"call_volume": "call volume", "csat_average_score": "CSAT average score"}


def _words(label_id: str) -> str:
    return label_id.replace("_", " ")


def _filters_text(filters: dict | None) -> str:
    if not filters:
        return "no filter"
    return "; ".join(f"{k} in {', '.join(v)}" for k, v in filters.items())


def _numbers(metric: str, metrics: dict) -> str:
    if metric == "csat_average_score":
        avg, n = metrics["csat_average_score"], metrics["responses"]
        return (f"CSAT average score {avg['before']:.2f} ({n['before']} responses) before and "
                f"{avg['after']:.2f} ({n['after']} responses) after")
    calls = metrics["call_volume"]
    return f"call volume {calls['before']} calls before and {calls['after']} after"


def _drivers_text(event: dict, split: str) -> str:
    drivers = sorted(event["default_window"]["drivers"], key=lambda d: -len(d["after"]))
    if split == "heldout":
        # Held-out driver wording stays out of committed eval sets: counts only.
        counts = ", ".join(str(len(d["after"])) for d in drivers)
        return (f"{len(drivers)} planted drivers hold {counts} of the after-window calls; "
                "they are scored by call id against the answer key")
    listed = "; ".join(f"{_words(d['id'])} ({len(d['after'])} calls)" for d in drivers)
    return f"planted drivers in the after window: {listed}"


def _patterns_text(event: dict) -> str:
    parts = []
    for p in event["default_window"]["patterns"]:
        calls = sorted({c for ids in p["variants"].values() for c in ids})
        ways = ", ".join(f'"{_words(v)}"' for v in p["variants"])
        parts.append(f"{_words(p['id'])}: across {len(calls)} {_words(p['driver'])} calls the agent "
                     f"states the rule {len(p['variants'])} different ways ({ways})")
    return "; ".join(parts)


def _reference(spec: dict, event: dict, split: str, metric: str, filters, source: str) -> str:
    dw = event["default_window"]
    when = {"logged": "named by the logged change",
            "period": "the one logged change inside the period the question gives",
            "detected": "found by detection and snapped to the logged change (within 4 days)"}
    head = f'"{spec["event"]}" on {event["date"]}, {when[source]}. '
    if spec["follow_up_of"]:
        return (head + f"A follow-up to {spec['follow_up_of']} on the same thread: the answer reuses "
                "that analysis and names coaching patterns across calls, never an agent. Planted "
                f"pattern, {_patterns_text(event)}.")
    span = (f"windows [{dw['before']['start']}, {dw['before']['end']}) and "
            f"[{dw['after']['start']}, {dw['after']['end']}) with {_filters_text(dw['filters'])}: "
            f"{_numbers(metric, dw['metrics'])}; {_drivers_text(event, split)}.")
    window = f"Default {span}" if filters == dw["filters"] else (
        "The question names no call type, so the tool picks the types whose volume rose. "
        f"For reference, the event's default {span}")
    notes = " ".join(event.get("notes") or [])
    return (head + window + (f" {notes}" if notes else "")
            + " A good answer reports both governed values with their SQL, gives each driver as "
              "n of M calls with citations from the windows, and says the timing fits the change "
              "but the data cannot show it caused the shift.")


def build(split: str = "golden", corpus=None) -> list[dict]:
    corpus = corpus or change_corpus.default_corpus()
    specs, _ = SETS[split]
    want = "golden" if split == "golden" else "heldout"
    records: dict[str, dict] = {}
    for spec in specs:
        event = corpus.event(spec["event"])
        if event["split"] != want:
            raise SystemExit(f"{spec['id']}: a {want} spec on a {event['split']} event "
                             f'("{spec["event"]}"); move it to the other spec file')
        base = records.get(spec["follow_up_of"]) if spec["follow_up_of"] else None
        if spec["follow_up_of"] and base is None:
            raise SystemExit(f"{spec['id']}: follows {spec['follow_up_of']}, which must come first")
        metric = base["metric_names"][0] if base else spec["metric"]
        filters = base["filters"] if base else spec["filters"]
        source = base["change_point_source"] if base else spec["change_point_source"]
        assert source in EXPECTED_SOURCE, f"{spec['id']}: unknown change_point_source {source!r}"
        how = f"event date {event['date']} and default window from ground_truth/events.json"
        if base:
            how = f"metric, filters and change point from {base['id']}; pattern calls from the ground truth"
        records[spec["id"]] = {
            "id": spec["id"], "kind": "change", "event": spec["event"],
            "question": spec["question"], "follow_up_of": spec["follow_up_of"],
            "route": "explain_change", "metric_names": [metric], "filters": filters,
            "change_point": event["date"], "change_point_source": source,
            "expect_interrupt": spec["expect_interrupt"],
            "evaluators": [*BASE_EVALUATORS, *spec["evaluators"]],
            "reference": _reference(spec, event, want, metric, filters, source),
            "derivation": how,
        }
    rows = list(records.values())
    assert len({r["question"] for r in rows}) == len(rows), "duplicate question"
    return rows


def frames() -> dict[str, dict]:
    """{question: the frame its spec scripts}, for the offline classifier double."""
    return {s["question"]: s["frame"] for s in (*CHANGE_SPECS, *HOLDOUT_SPECS)}


def dumps(records: list[dict]) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1].strip())
    parser.add_argument("--check", action="store_true",
                        help="fail if a dataset file differs from a fresh derivation")
    parser.add_argument("--dataset", choices=sorted(SETS), default=None,
                        help="golden (change_golden.jsonl) or holdout (change_holdout.jsonl); "
                             "--check checks both by default")
    args = parser.parse_args(argv)

    names = [args.dataset] if args.dataset else (sorted(SETS) if args.check else ["golden"])
    stale = 0
    for name in names:
        text, path = dumps(build(name)), SETS[name][1]
        if args.check:
            ok = path.exists() and path.read_text() == text
            stale += not ok
            print(f"{path.name} is {'up to date' if ok else 'stale'}"
                  + ("" if ok else f"; run python -m evals.datasets.build_change --dataset {name}"))
            continue
        path.write_text(text)
        print(f"wrote {text.count(chr(10))} items to {path}")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
