"""Before/after comparison of two `make evals-live` result files.

    make evals-compare A=results/evals/<before>.json B=results/evals/<after>.json
    python -m evals.compare <before.json> <after.json>     # B may be omitted: LATEST

Prints what each side ran (question set, groups, agent model, judge model, judge prompt,
golden set hash, item count); it refuses two runs over different question sets (golden vs
holdout) unless --allow-different-datasets,
a warning for every one of those that differs, then one table per group (all, then each
golden kind) with each check's pass rate on both sides and the delta. A changed judge
model or prompt makes the judge column incomparable; a changed golden set or --limit makes
every column incomparable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LATEST_PATH = REPO_ROOT / "results" / "evals" / "LATEST"  # written by evals.run

# What a side ran: label -> (section, key) in the run record.
SETUP = {
    "dataset": ("params", "eval_dataset"),
    "groups": ("params", "groups"),
    "agent model": ("versions", "agent_model"),
    "retriever": ("params", "retriever_backend"),
    "search mode": ("params", "retrieval_mode"),
    "search k": ("params", "retrieval_k"),
    "judge model": ("versions", "judge_model"),
    "judge prompt": ("params", "judge_prompt"),
    "golden set": ("params", "golden_sha256"),
    "limit": ("params", "limit"),
}
# A difference in these makes the named checks incomparable (None: every check).
AFFECTS = {"judge model": ["judge"], "judge prompt": ["judge"], "golden set": None, "limit": None,
           "groups": "groups"}


class DifferentDatasets(SystemExit):
    """Two runs over different question sets (golden vs holdout) have no before/after."""


TOOL_ERROR_MARKERS = ("TOOL ERROR (", "query_metric error:", "is already defined for this MetaData instance")


def tool_errors(record: dict) -> dict[str, int] | None:
    """Items whose answer is a tool failure, per group (and "all"). Counted from `items`
    when the file has them, so runs recorded before the count existed are covered too."""
    items = record.get("items")
    if items is not None:
        counts: dict[str, int] = {"all": 0}
        for it in items:
            answer = (it.get("outputs") or {}).get("answer") or ""
            hit = bool(it.get("tool_error")) or any(m in answer for m in TOOL_ERROR_MARKERS)
            counts["all"] += hit
            counts[it.get("kind")] = counts.get(it.get("kind"), 0) + hit
        return counts
    rows = {g: r.get("tool_errors") for g, r in record["metrics"].items() if isinstance(r, dict)}
    return rows if any(v is not None for v in rows.values()) else None


def load(path: Path) -> dict:
    record = json.loads(Path(path).read_text())
    if record.get("area") != "evals":
        raise SystemExit(f"{path}: not an evals result (area={record.get('area')!r})")
    return record


def setup(record: dict) -> dict[str, object]:
    out = {label: record.get(section, {}).get(key) for label, (section, key) in SETUP.items()}
    out["dataset"] = out["dataset"] or "golden"  # runs before --dataset existed were golden
    out["groups"] = ",".join(out["groups"]) if out["groups"] else "all"
    # Runs before these were recorded searched pgvector, vector mode, k=5.
    out["retriever"] = out["retriever"] or "pgvector"
    out["search mode"] = out["search mode"] or "vector"
    out["search k"] = out["search k"] or 5
    return out


def warnings(a: dict, b: dict) -> list[str]:
    sa, sb = setup(a), setup(b)
    out = []
    for label in SETUP:
        if sa[label] != sb[label]:
            what = AFFECTS.get(label, [])
            consequence = ("compare only the tables of groups both runs scored; 'all' is not comparable"
                           if what == "groups"
                           else "every score is incomparable" if what is None
                           else f"{', '.join(what)} scores are not comparable" if what
                           else "the delta measures this change")
            out.append(f"WARNING: {label} differs ({_short(sa[label])} -> {_short(sb[label])}): {consequence}")
    return out


def _short(value) -> str:
    text = "none" if value is None else str(value)
    return text[:12] if len(text) == 64 else text  # a sha256 reads fine as its first 12


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def _delta(a, b) -> str:
    if a is None or b is None:
        return ""
    d = b - a
    return f"{d:+.2f}" if abs(d) >= 0.005 else "="


def checks(a: dict, b: dict) -> list[str]:
    keys: list[str] = []
    for record in (a, b):
        for row in record["metrics"].values():
            if isinstance(row, dict):
                keys += [k for k in row if k != "n" and k not in keys]
    return keys


def group_table(group: str, a: dict, b: dict, keys: list[str],
                te: tuple[dict | None, dict | None] = (None, None)) -> str:
    ra, rb = a["metrics"].get(group) or {}, b["metrics"].get(group) or {}
    lines = [f"{group}  (n {ra.get('n', 0)} -> {rb.get('n', 0)})",
             f"  {'check':<10} {'before':>7} {'after':>7} {'delta':>7}"]
    for k in keys:
        lines.append(f"  {k:<10} {_fmt(ra.get(k)):>7} {_fmt(rb.get(k)):>7} {_delta(ra.get(k), rb.get(k)):>7}")
    ta, tb = (t.get(group) if t else None for t in te)
    if ta or tb:
        show = lambda v: "?" if v is None else str(v)  # noqa: E731
        lines.append(f"  {'tool err':<10} {show(ta):>7} {show(tb):>7}   items whose answer is a tool failure")
    return "\n".join(lines)


def compare(a: dict, b: dict, name_a: str = "A", name_b: str = "B", allow_datasets: bool = False) -> str:
    da, db = setup(a)["dataset"], setup(b)["dataset"]
    if da != db and not allow_datasets:
        raise DifferentDatasets(
            f"evals-compare: refusing: {name_a} ran the {da} set and {name_b} the {db} set, so no "
            "score has a before/after; compare runs of the same set (or pass --allow-different-datasets "
            "to print them side by side anyway)")
    parts = []
    if da != db:
        parts.append(f"!!! DIFFERENT QUESTION SETS ({da} vs {db}): NO SCORE BELOW IS A BEFORE/AFTER !!!")
    width = max(len(label) for label in SETUP)
    for name, record in ((name_a, a), (name_b, b)):
        s = setup(record)
        parts.append(f"{name}: {record['date']} {record['run']} @ {record['git_sha'][:7]}\n" + "\n".join(
            f"  {label:<{width}}  {_short(s[label])}" for label in SETUP))
    warn = warnings(a, b)
    if warn:
        parts.append("\n".join(warn))
    keys = [k for k in checks(a, b) if k != "tool_errors"]
    te = (tool_errors(a), tool_errors(b))
    for name, counts in ((name_a, te[0]), (name_b, te[1])):
        if counts and counts.get("all"):
            parts.append(f"WARNING: {name} has {counts['all']} item(s) whose answer is a tool failure; "
                         "their scores measure the failure, not the agent")
    groups = [g for g in dict.fromkeys([*a["metrics"], *b["metrics"]])
              if isinstance(a["metrics"].get(g, b["metrics"].get(g)), dict)]
    parts += [group_table(g, a, b, keys, te) for g in groups]
    ea, eb = a["metrics"].get("errors"), b["metrics"].get("errors")
    if ea is not None or eb is not None:
        parts.append(f"errors: {ea} -> {eb}")
    return "\n\n".join(parts)


def latest(results_dir: Path | None = None) -> Path:
    pointer = (results_dir / LATEST_PATH.name) if results_dir else LATEST_PATH
    if not pointer.exists():
        raise SystemExit(f"no {pointer.relative_to(REPO_ROOT) if pointer.is_relative_to(REPO_ROOT) else pointer}:"
                         " pass B=<file>, or run make evals-live first")
    return pointer.parent / pointer.read_text().strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two evals-live result files.")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path, nargs="?", help="default: the file results/evals/LATEST names")
    parser.add_argument("--allow-different-datasets", action="store_true",
                        help="print a golden run beside a holdout run (no score is a before/after)")
    args = parser.parse_args(argv)
    after = args.after or latest()
    print(compare(load(args.before), load(after), str(args.before), str(after), args.allow_different_datasets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
