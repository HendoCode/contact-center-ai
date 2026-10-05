"""Export one LangSmith evals experiment's per-item results to JSON (read-only).

    make evals-export EXP=agent-golden-openai-74042631 [OUT=<file>]   # default results/evals/items/<EXP>.json

For runs made before `make evals-live` kept per-item detail in its own results file. Each
item has the same shape as a results file's `items`: golden id and kind, the inputs, the
agent's outputs (answer, route, metric names, SQL, citations, grounded), the run error, and
every evaluator's score and comment, including the judge's score and reasoning. Needs
LANGSMITH_API_KEY; it only reads from LangSmith.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from evals.run import OUTPUT_KEYS, REPO_ROOT

# Outside results/evals/*.json, which holds only aggregate run records (tools.results renders them).
ITEMS_DIR = REPO_ROOT / "results" / "evals" / "items"


def export(client, experiment: str) -> dict:
    """Per-item results for every root run of `experiment`, sorted by golden id."""
    runs = list(client.list_runs(project_name=experiment, is_root=True))
    if not runs:
        raise SystemExit(f"LangSmith experiment {experiment!r} has no runs (check the name)")
    feedback: dict[str, list] = defaultdict(list)
    for fb in client.list_feedback(run_ids=[r.id for r in runs]):
        feedback[str(fb.run_id)].append(fb)
    items = []
    for run in runs:
        meta = {}
        if run.reference_example_id:
            meta = client.read_example(run.reference_example_id).metadata or {}
        outputs = run.outputs or {}
        items.append({
            "id": meta.get("golden_id"), "kind": meta.get("kind"),
            "inputs": run.inputs or {}, "error": run.error,
            "outputs": {k: outputs.get(k) for k in OUTPUT_KEYS},
            "results": {fb.key: {"key": fb.key, "score": fb.score, "comment": fb.comment or ""}
                        for fb in sorted(feedback[str(run.id)], key=lambda f: f.key)},
        })
    items.sort(key=lambda i: (i["id"] is None, str(i["id"])))
    return {"experiment": experiment, "n": len(items), "items": items}


def main(argv: list[str] | None = None, client=None) -> int:
    parser = argparse.ArgumentParser(description="Export a LangSmith evals experiment's per-item results.")
    parser.add_argument("experiment", help="LangSmith experiment name, e.g. agent-golden-openai-74042631")
    parser.add_argument("--out", type=Path, help="output file (default results/evals/items/<experiment>.json)")
    args = parser.parse_args(argv)
    out = args.out or ITEMS_DIR / f"{args.experiment}.json"
    if out.exists():
        raise SystemExit(f"{out} exists; pass OUT=<another file> (exports never overwrite)")
    if client is None:
        import os

        from dotenv import load_dotenv

        load_dotenv()
        if not os.getenv("LANGSMITH_API_KEY"):
            raise SystemExit("make evals-export reads from LangSmith: set LANGSMITH_API_KEY")
        from langsmith import Client

        client = Client()
    data = export(client, args.experiment)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {out} ({data['n']} items from {args.experiment})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
