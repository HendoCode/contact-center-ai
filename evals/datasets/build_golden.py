"""
Derive the L2 agent golden set from committed data and generator output.

    python -m evals.datasets.build_golden           # rewrite agent_golden.jsonl
    python -m evals.datasets.build_golden --check   # exit 1 if agent_golden.jsonl is stale

Only the questions are written by hand (SPECS below). Every expectation is computed
here, by code, from three sources, and never from running the agent:

- `olap/dbt/models/marts/semantic/metrics.yml`: which declared metric a question names.
  A question names a metric when it contains the metric's label (lowercased, "Avg"
  spelled "average", any "(...)" and "%" dropped) as a whole phrase.
- `agent/data/ambiguous_terms.yml`: whether a question should interrupt. The file's
  documented semantics are re-read here independently of `agent/terms.py`: a term
  fires on a whole-word alias left after its `unless` phrases are removed. A term is
  settled when the question names one of its candidates by label, or when exactly one
  candidate has a qualifier in the question. Otherwise the agent should ask, offering
  the candidates with a qualifier in the question, or all of them when none has one.
- The seed-42 generator's `transcripts.json`: which call ids exist, the facts of each
  call-lookup id (picked by a predicate, lowest id first), and the categories behind
  each open question.

Run `make seed` first; the generator is deterministic, so the output is too.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "synthetic"
METRICS_PATH = REPO_ROOT / "olap" / "dbt" / "models" / "marts" / "semantic" / "metrics.yml"
TERMS_PATH = REPO_ROOT / "agent" / "data" / "ambiguous_terms.yml"
GOLDEN_PATH = Path(__file__).resolve().parent / "agent_golden.jsonl"
HOLDOUT_PATH = Path(__file__).resolve().parent / "agent_holdout.jsonl"

KINDS = ("ambiguous", "metric", "call_lookup", "open")
ROUTE_BY_KIND = {
    "ambiguous": "resolve_metric", "metric": "resolve_metric",
    "call_lookup": "summarize_call", "open": "retrieve",
}

# ── question specs ────────────────────────────────────────────────────────────
# ambiguous: `clarify_with` is what the user means, i.e. the answer they give when the
#            agent asks. It must be among the options derived for each interrupt.
# metric:    nothing but the question; the metric names are derived from it, and become
#            its `clarify_with` too, so an interrupt that should not have fired is
#            answered with what the user meant and costs only the interrupt check.
# call_lookup: `pick` is a predicate over transcripts.json; the lowest matching call id
#            fills `{call_id}`. "missing" picks the first id past the end of the corpus.
#            `lowercase` writes the id as a supervisor might type it.
# open:      `categories` are the generator categories whose calls answer the question.

SPECS: list[dict] = [
    # ambiguous: a bare term, so the agent must ask instead of guessing.
    {"kind": "ambiguous", "question": "What is our average rate?",
     "clarify_with": ["average_mortgage_note_rate"]},
    {"kind": "ambiguous", "question": "What rates are we paying members?",
     "clarify_with": ["average_deposit_apy"]},
    {"kind": "ambiguous", "question": "What is the average mortgage rate?",
     "clarify_with": ["weighted_mortgage_portfolio_rate"]},
    {"kind": "ambiguous", "question": "What is the average rate on mortgage and HELOC accounts?",
     "clarify_with": ["average_mortgage_note_rate", "average_heloc_current_rate"]},
    {"kind": "ambiguous", "question": "What is the total balance?",
     "clarify_with": ["savings_balance"]},
    {"kind": "ambiguous", "question": "What are member balances looking like this quarter?",
     "clarify_with": ["banking_ledger_balance"]},
    {"kind": "ambiguous", "question": "How are balances trending?",
     "clarify_with": ["mortgage_principal_balance"]},
    {"kind": "ambiguous", "question": "What is our LCV?",
     "clarify_with": ["member_lifetime_value"]},
    {"kind": "ambiguous", "question": "What's the average LTV?",
     "clarify_with": ["loan_to_value"]},
    {"kind": "ambiguous", "question": "What is the LTV on our loans?",
     "clarify_with": ["loan_to_value"]},
    {"kind": "ambiguous", "question": "What are our average rate and our total balance?",
     "clarify_with": ["average_deposit_apy", "savings_balance"]},

    # metric: names a declared metric by label, or settles a term with one qualifier.
    {"kind": "metric", "question": "What was our call volume?"},
    {"kind": "metric", "question": "What is our average handle time?"},
    {"kind": "metric", "question": "What is our first contact resolution rate?"},
    {"kind": "metric", "question": "What is our CSAT average score?"},
    {"kind": "metric", "question": "What is our net promoter score?"},
    {"kind": "metric", "question": "How many rate locks expired?"},
    {"kind": "metric", "question": "What is the rate lock fallout percentage?"},
    {"kind": "metric", "question": "What is the average mortgage note rate?"},
    {"kind": "metric", "question": "What is the average HELOC rate?"},
    {"kind": "metric", "question": "What is the average savings rate?"},
    {"kind": "metric", "question": "What is the total escrow balance?"},
    {"kind": "metric", "question": "What is the total credit card balance?"},
    {"kind": "metric", "question": "What is the banking ledger balance?"},
    {"kind": "metric", "question": "What is the checking available balance?"},
    {"kind": "metric", "question": "What is the member lifetime value (LCV)?"},
    {"kind": "metric", "question": "What is the average loan to value (LTV)?"},
    {"kind": "metric", "question": "How many active members do we have?"},
    {"kind": "metric",
     "question": "What are the average HELOC rate and the HELOC drawn balance?"},

    # call_lookup: one call by id. The id, not the model, decides the route.
    {"kind": "call_lookup", "question": "Summarize {call_id}.",
     "pick": {"category": "fraud_dispute", "outcome": "escalated"}},
    {"kind": "call_lookup", "question": "What happened on {call_id}?",
     "pick": {"category": "payment_assistance", "outcome": "unresolved"}},
    {"kind": "call_lookup", "question": "Was {call_id} resolved?",
     "pick": {"category": "account_balance", "outcome": "resolved"}},
    {"kind": "call_lookup", "question": "Why did the member call in {call_id}?",
     "pick": {"category": "escrow_analysis"}},
    {"kind": "call_lookup", "question": "Give me the outcome of {call_id}.",
     "pick": {"category": "rate_lock_status", "outcome": "escalated"}},
    {"kind": "call_lookup", "question": "Summarize {call_id}, the call about a loan rate.",
     "pick": {"category": "rate_inquiry"}},
    {"kind": "call_lookup", "question": "What balance was discussed in {call_id}?",
     "pick": {"category": "account_balance", "outcome": "transferred"}},
    {"kind": "call_lookup", "question": "Which agent handled {call_id}?",
     "pick": {"category": "investment_review"}},
    {"kind": "call_lookup", "question": "what happened on {call_id}?", "lowercase": True,
     "pick": {"category": "fee_dispute", "outcome": "escalated"}},
    {"kind": "call_lookup", "question": "Summarize {call_id}.", "pick": "missing"},

    # open: answered from transcripts, with call ids as citations.
    {"kind": "open", "question": "What are members saying when they call about fraud disputes?",
     "categories": ["fraud_dispute"]},
    {"kind": "open", "question": "Why do members call to dispute fees?",
     "categories": ["fee_dispute"]},
    {"kind": "open", "question": "What problems do members have logging in to online banking?",
     "categories": ["online_banking_support"]},
    {"kind": "open", "question": "What do members ask when they call to check their balance?",
     "categories": ["account_balance"]},
    {"kind": "open", "question": "What do members ask when they check on a mortgage rate lock?",
     "categories": ["rate_lock_status"]},
    {"kind": "open", "question": "What do members ask about the interest rates on their loans?",
     "categories": ["rate_inquiry", "loan_inquiry"]},
    {"kind": "open", "question": "How do agents handle members who can't make a payment?",
     "categories": ["payment_assistance"]},
    {"kind": "open", "question": "What do members want to know when they open a new account?",
     "categories": ["account_opening"]},
    {"kind": "open", "question": "What issues come up on escrow analysis calls?",
     "categories": ["escrow_analysis"]},
    {"kind": "open", "question": "What do members ask about their insurance policies?",
     "categories": ["insurance_service"]},
    {"kind": "open", "question": "What do members ask during investment reviews?",
     "categories": ["investment_review"]},
    {"kind": "open", "question": "What card services requests do members make?",
     "categories": ["card_services"]},
]


# ── committed data ────────────────────────────────────────────────────────────

def load_metrics(path: Path = METRICS_PATH) -> dict[str, str]:
    """Declared metric name -> label, in metrics.yml order."""
    with open(path, encoding="utf-8") as f:
        return {m["name"]: m.get("label", m["name"]) for m in yaml.safe_load(f)["metrics"]}


def load_terms(path: Path = TERMS_PATH) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["terms"]


def load_calls(data_dir: Path = DATA_DIR) -> list[dict]:
    """transcripts.json without the transcript text, in call id order."""
    path = data_dir / "transcripts.json"
    if not path.exists():
        sys.exit(f"{path} not found. Run `make seed` first.")
    keep = ("call_id", "date", "duration_seconds", "category", "outcome", "member_id",
            "agent_id")
    calls = [{k: t[k] for k in keep} for t in json.loads(path.read_text())]
    return sorted(calls, key=lambda c: c["call_id"])


# ── question analysis ─────────────────────────────────────────────────────────

def _phrase(text: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(text.lower())}\b")


def label_core(label: str) -> str:
    """The phrase a question must contain to name a metric: "Avg Deposit APY" ->
    "average deposit apy", "Average Handle Time (AHT)" -> "average handle time"."""
    text = re.sub(r"\(.*?\)", " ", label).replace("%", " ")
    text = re.sub(r"\bavg\b", "average", text, flags=re.IGNORECASE)
    return " ".join(text.lower().split())


def named_metrics(question: str, metrics: dict[str, str]) -> list[str]:
    """Metrics whose label the question contains, minus labels inside a longer match."""
    q = question.lower()
    spans = {}
    for name, label in metrics.items():
        m = _phrase(label_core(label)).search(q)
        if m:
            spans[name] = m.span()
    inside = {
        a for a, (s, e) in spans.items()
        for b, (s2, e2) in spans.items() if a != b and s2 <= s and e <= e2 and (s, e) != (s2, e2)
    }
    return sorted((n for n in spans if n not in inside), key=lambda n: spans[n][0])


def terms_in(question: str, terms: dict[str, dict]) -> list[str]:
    """Terms whose alias appears once their `unless` phrases are removed, by position."""
    q = question.lower()
    found = []
    for name, spec in terms.items():
        stripped = q
        for phrase in spec.get("unless") or ():
            stripped = _phrase(phrase).sub(" ", stripped)
        starts = [m.start() for a in spec["aliases"] if (m := _phrase(a).search(stripped))]
        if starts:
            found.append((min(starts), name))
    return [name for _, name in sorted(found)]


def expected_resolution(question: str, metrics, terms) -> tuple[list[str], list[dict]]:
    """(metric names settled by the question itself, interrupts the agent should raise)."""
    q = question.lower()
    names = named_metrics(question, metrics)
    interrupts = []
    for term in terms_in(question, terms):
        candidates = terms[term]["candidates"]
        if any(m in names for m in candidates):
            continue
        hits = [m for m, quals in candidates.items() if any(_phrase(x).search(q) for x in quals)]
        if len(hits) == 1:
            names.append(hits[0])
        else:
            interrupts.append({"term": term, "options": hits or list(candidates)})
    return names, interrupts


# ── records ───────────────────────────────────────────────────────────────────

def _metric_record(spec: dict, metrics, terms) -> dict:
    question = spec["question"]
    names, interrupts = expected_resolution(question, metrics, terms)
    clarify = spec.get("clarify_with")
    if spec["kind"] == "metric":
        assert not interrupts, f"{question!r} should be unambiguous, derives {interrupts}"
        assert names, f"{question!r} names no declared metric"
        assert clarify is None, f"{question!r}: a metric question takes no clarify_with"
        clarify = names
        how = "names " + ", ".join(names)
    else:
        assert interrupts, f"{question!r} should be ambiguous, derives metrics {names}"
        assert clarify and all(m in metrics for m in clarify), f"{question!r}: bad clarify_with"
        offered = {o for i in interrupts for o in i["options"]}
        assert set(clarify) <= offered, f"{question!r}: {clarify} not among the options"
        for i in interrupts:
            assert set(clarify) & set(i["options"]), f"{question!r}: no answer for {i['term']}"
        names = list(dict.fromkeys([*names, *clarify]))
        how = "; ".join(
            f'term "{i["term"]}" leaves {len(i["options"])} candidates' for i in interrupts
        ) + f"; user answers {', '.join(clarify)}"

    labels = ", ".join(f"{n} ({metrics[n]})" for n in names)
    reference = (
        f"Answered by the declared metric(s) {labels}, each reported as its own number "
        "together with the SQL MetricFlow generated. No blended or invented figures."
    )
    if interrupts:
        reference = (
            " ".join(
                f'"{i["term"]}" matches {len(i["options"])} declared metrics, so the agent '
                "asks which one is meant before answering." for i in interrupts
            )
            + f" The user chose {', '.join(clarify)}. " + reference
        )
    return {
        "question": question, "clarify_with": clarify, "metric_names": names,
        "interrupts": interrupts, "call_id": None, "categories": [], "expect_citations": False, "expect_sql": True,
        "reference": reference, "derivation": how,
    }


def _call_record(spec: dict, calls: list[dict]) -> dict:
    by_id = {c["call_id"]: c for c in calls}
    if spec["pick"] == "missing":
        call_id = f"CALL-{len(calls) + 1:05d}"
        assert call_id not in by_id
        call = None
        how = f"first id past the corpus end ({calls[0]['call_id']}..{calls[-1]['call_id']})"
    else:
        pool = [c for c in calls if all(c[k] == v for k, v in spec["pick"].items())]
        assert pool, f"no call matches {spec['pick']}"
        call = pool[0]
        call_id = call["call_id"]
        how = "lowest call_id with " + " AND ".join(f"{k} = {v}" for k, v in spec["pick"].items())
    typed = call_id.lower() if spec.get("lowercase") else call_id
    if call is None:
        reference = (
            f"{call_id} does not exist (the corpus runs {calls[0]['call_id']} to "
            f"{calls[-1]['call_id']}). The answer says no such call was found and invents nothing."
        )
    else:
        reference = (
            f"{call_id}: {call['date'][:10]}, category {call['category']}, outcome "
            f"{call['outcome']}, {call['duration_seconds']} seconds, member {call['member_id']}, "
            f"agent {call['agent_id']}."
        )
    return {
        "question": spec["question"].format(call_id=typed), "clarify_with": None,
        "metric_names": [], "interrupts": [], "call_id": call_id, "categories": [],
        "expect_citations": call is not None, "expect_sql": False,
        "reference": reference, "derivation": how,
    }


def _open_record(spec: dict, calls: list[dict], metrics) -> dict:
    counts = Counter(c["category"] for c in calls)
    cats = spec["categories"]
    assert all(counts[c] for c in cats), f"unknown category in {cats}"
    assert not named_metrics(spec["question"], metrics), f"{spec['question']!r} names a metric"
    sizes = ", ".join(f"{c} ({counts[c]} calls)" for c in cats)
    # Open questions route to retrieve, where no interrupt can fire, even when they use
    # a term ("balance", "rates"); `interrupts` stays empty for them.
    return {
        "question": spec["question"], "clarify_with": None, "metric_names": [],
        "interrupts": [], "call_id": None, "categories": cats, "expect_citations": True, "expect_sql": False,
        "reference": (
            f"Relevant calls are in {sizes}. A good answer describes what those calls "
            "contain, cites real call ids, and adds nothing the transcripts do not support."
        ),
        "derivation": "categories " + ", ".join(cats),
    }


def build(calls: list[dict] | None = None, specs: list[dict] | None = None,
          prefix: str = "g") -> list[dict]:
    calls = calls if calls is not None else load_calls()
    metrics, terms = load_metrics(), load_terms()
    records = []
    for n, spec in enumerate(SPECS if specs is None else specs, start=1):
        kind = spec["kind"]
        if kind in ("ambiguous", "metric"):
            body = _metric_record(spec, metrics, terms)
        elif kind == "call_lookup":
            body = _call_record(spec, calls)
        else:
            body = _open_record(spec, calls, metrics)
        records.append({
            "id": f"{prefix}{n:02d}", "kind": kind, "question": body["question"],
            "clarify_with": body["clarify_with"],
            "route": ROUTE_BY_KIND[kind],
            "expect_interrupt": bool(body["interrupts"]),
            "interrupts": body["interrupts"],
            "metric_names": body["metric_names"],
            "expect_sql": body["expect_sql"],
            "call_id": body["call_id"],
            "expect_citations": body["expect_citations"],
            "categories": body["categories"],
            "reference": body["reference"],
            "derivation": body["derivation"],
        })
    questions = [r["question"] for r in records]
    assert len(set(questions)) == len(questions), "duplicate question"
    return records


def dumps(records: list[dict]) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


def build_dataset(name: str, calls: list[dict] | None = None) -> tuple[list[dict], Path]:
    """The records and file for a named dataset: golden (g01..) or holdout (h01..)."""
    if name == "holdout":
        from evals.datasets.holdout_specs import HOLDOUT_SPECS

        return build(calls, HOLDOUT_SPECS, prefix="h"), HOLDOUT_PATH
    return build(calls), GOLDEN_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1].strip())
    parser.add_argument("--check", action="store_true",
                        help="fail if the dataset file differs from a fresh derivation")
    parser.add_argument("--dataset", choices=("golden", "holdout"), default="golden",
                        help="golden (agent_golden.jsonl, default) or holdout (agent_holdout.jsonl)")
    args = parser.parse_args(argv)

    records, path = build_dataset(args.dataset)
    text = dumps(records)
    if args.check:
        if not path.exists() or path.read_text() != text:
            print(f"{path} is stale; run python -m evals.datasets.build_golden --dataset {args.dataset}")
            return 1
        print(f"{path} is up to date")
        return 0
    path.write_text(text)
    counts = Counter(json.loads(line)["kind"] for line in text.splitlines())
    print(f"wrote {sum(counts.values())} items to {path}: {dict(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
