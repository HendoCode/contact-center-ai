"""Before/after comparison of two `make evals-live` result files.

    make evals-compare A=open-pgvector-vector B=open-lance-hybrid   # run names
    make evals-compare A=latest~1 B=latest                          # the two newest runs
    make evals-compare A=results/evals/baseline-2026-10-05-pre-fix.json   # a path; B: latest

A and B are each a run name (the newest results file of that `--run`), `latest` or
`latest~N` (N runs before the newest), or a path. It prints which file each resolved to.

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
import re
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


RESULTS_DIR = LATEST_PATH.parent
# <UTC date-time>_<run>_<short sha>[-n].json, as evals.run.result_path writes them
_RUN_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{6}Z)_(.+)_([0-9a-f]{7})(?:-\d+)?\.json$")


def run_files(results_dir: Path | None = None) -> list[tuple[str, str, Path]]:
    """(timestamp, run name, path) of every timestamped results file, newest first."""
    out = []
    for p in (results_dir or RESULTS_DIR).glob("*.json"):
        m = _RUN_FILE.match(p.name)
        if m:
            out.append((m.group(1), m.group(2), p))
    return sorted(out, key=lambda t: (t[0], t[2].name), reverse=True)


def latest(results_dir: Path | None = None) -> Path:
    return resolve("latest", results_dir)


def resolve(spec: str, results_dir: Path | None = None) -> Path:
    """A run name, `latest`, `latest~N` or a path -> one results file; one clear line if not."""
    results_dir = results_dir or RESULTS_DIR
    path = Path(spec)
    if path.suffix == ".json" and path.exists():
        return path
    if (results_dir / spec).is_file():
        return results_dir / spec
    files = run_files(results_dir)
    m = re.fullmatch(r"latest(?:~(\d+))?", spec)
    if m:
        n = int(m.group(1) or 0)
        if n >= len(files):
            raise SystemExit(f"evals-compare: {spec}: only {len(files)} run(s) in {results_dir.name}/")
        return files[n][2]
    named = [f for f in files if f[1] == spec]
    if not named:
        names = sorted({f[1] for f in files})
        raise SystemExit(f"evals-compare: no run named {spec!r}; runs: {', '.join(names) or 'none'}")
    if len(named) > 1 and named[0][0][:-3] == named[1][0][:-3]:  # same minute: ambiguous
        same = ", ".join(f[2].name for f in named if f[0][:-3] == named[0][0][:-3])
        raise SystemExit(f"evals-compare: {spec!r} has more than one run in the same minute ({same}); "
                         "pass one of these file names")
    return named[0][2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two evals-live result files.")
    parser.add_argument("before", help="run name, latest, latest~N, or a results file path")
    parser.add_argument("after", nargs="?", default="latest", help="same forms; default latest")
    parser.add_argument("--allow-different-datasets", action="store_true",
                        help="print a golden run beside a holdout run (no score is a before/after)")
    args = parser.parse_args(argv)
    before, after = resolve(args.before), resolve(args.after)
    for label, spec, path in (("A", args.before, before), ("B", args.after, after)):
        shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
        print(f"{label}: {spec} -> {shown}")
    print()
    print(compare(load(before), load(after), before.name, after.name, args.allow_different_datasets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
