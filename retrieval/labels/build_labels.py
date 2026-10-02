"""
Derive the R3 retrieval-benchmark labels from generator metadata.

    python -m retrieval.labels.build_labels           # rewrite queries.jsonl
    python -m retrieval.labels.build_labels --check   # exit 1 if queries.jsonl is stale

Every relevant set is computed here, by code, from the seed-42 generator's own
outputs (`transcripts.json`, `interaction_accounts.json`, `accounts.json`,
`products.json`) and its dialogue templates (`CATEGORY_DIALOGUES`). No retriever is
imported or run. A query's relevance is a predicate over these per-call facts:

    category, outcome, date (ISO datetime), member_id, subject account id,
    subject product LOB, and which template placeholders the call's category speaks.

The `where` attached to a filter-heavy query is the structured part of the request
(outcome, date, sometimes category) that the transcript text cannot express. The
relevant set is the full predicate, so a backend must both prefilter correctly and
rank the right category first inside the filtered pool.

Run `make seed` first; the generator is deterministic, so the output is too.
"""

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "synthetic"
LABELS_PATH = Path(__file__).resolve().parent / "queries.jsonl"

QUERY_TYPES = ("topical", "filter", "exact", "paraphrase")

# Stored dates are datetimes ("2026-03-31T14:02:11") and both backends compare them
# as strings, so a date-only inclusive upper bound would drop the last day. Ranges
# therefore close on the last second of the day.
DAY_END = "T23:59:59"


def month(m: str) -> dict:
    """Inclusive date range covering calendar month `m` ("2026-03")."""
    last = {"02": 28, "04": 30, "06": 30, "09": 30, "11": 30}.get(m[5:], 31)
    return {"gte": f"{m}-01", "lte": f"{m}-{last:02d}{DAY_END}"}


def days(first: str, last: str) -> dict:
    return {"gte": first, "lte": f"{last}{DAY_END}"}


# ── query specs ───────────────────────────────────────────────────────────────
# `match` is the relevance predicate (keys: category, lob, outcome, date, speaks,
# member, account); `where` is what the bench passes to Retriever.search. Values
# may be a scalar (equality) or {"in": [...]}, and `date` a {"gte", "lte"} range,
# mirroring the portable `where` subset in retrieval/base.py.

# "Last week" relative to the generator's fixed REFERENCE_DATE (Tue 2026-09-01):
# the Monday-to-Sunday week before it.
LAST_WEEK = days("2026-08-24", "2026-08-30")

SPECS: list[dict] = [
    # topical: the request names the call's subject in the corpus's own vocabulary.
    {"type": "topical", "query": "member checking their account balance",
     "match": {"category": "account_balance"}},
    {"type": "topical", "query": "credit card limit, outstanding balance and available credit",
     "match": {"category": "card_services"}},
    {"type": "topical", "query": "disputing a charge from an online retailer",
     "match": {"category": "fraud_dispute"}},
    {"type": "topical", "query": "reviewing an auto insurance premium and deductible",
     "match": {"category": "insurance_service"}},
    {"type": "topical", "query": "escrow analysis shortage notice",
     "match": {"category": "escrow_analysis"}},
    {"type": "topical", "query": "when does my mortgage rate lock expire",
     "match": {"category": "rate_lock_status"}},
    {"type": "topical", "query": "opening a new savings account",
     "match": {"category": "account_opening"}},
    # topical + product: same template, told apart only by the subject account's LOB.
    {"type": "topical", "query": "what interest rate am I paying on my home equity line",
     "match": {"category": "rate_inquiry", "lob": "home"}},
    {"type": "topical", "query": "hardship deferral of a mortgage principal-and-interest payment",
     "match": {"category": "payment_assistance", "lob": "mortgage"}},
    {"type": "topical", "query": "can't afford my credit card minimum payment",
     "match": {"category": "payment_assistance", "lob": "cards"}},
    {"type": "topical", "query": "unrecognized charge on my checking account",
     "match": {"category": "fraud_dispute", "lob": "banking"}},

    # filter-heavy: outcome and date are never spoken, so they arrive as `where`.
    {"type": "filter", "query": "escalated fraud calls in March",
     "where": {"category": "fraud_dispute", "outcome": "escalated", "date": month("2026-03")}},
    {"type": "filter", "query": "unresolved online banking login problems in June",
     "where": {"category": "online_banking_support", "outcome": "unresolved",
               "date": month("2026-06")}},
    {"type": "filter", "query": "fraud disputes from last week",
     "match": {"category": "fraud_dispute"}, "where": {"date": LAST_WEEK}},
    {"type": "filter", "query": "escalated credit card limit questions in July",
     "match": {"category": "card_services"},
     "where": {"outcome": "escalated", "date": month("2026-07")}},
    {"type": "filter", "query": "payment hardship requests that were escalated or left unresolved this summer",
     "match": {"category": "payment_assistance"},
     "where": {"outcome": {"in": ["escalated", "unresolved"]},
               "date": days("2026-06-01", "2026-08-31")}},
    {"type": "filter", "query": "mortgage check-ins that needed a callback, April through June",
     "match": {"category": "mortgage_inquiry"},
     "where": {"outcome": "callback_scheduled", "date": days("2026-04-01", "2026-06-30")}},
    {"type": "filter", "query": "unresolved calls about loan rates",
     "match": {"category": "loan_inquiry"}, "where": {"outcome": "unresolved"}},
    {"type": "filter", "query": "escalated complaints about a monthly fee",
     "match": {"category": "fee_dispute"}, "where": {"outcome": "escalated"}},
    {"type": "filter", "query": "auto insurance policy reviews transferred in March or April",
     "match": {"category": "insurance_service"},
     "where": {"outcome": "transferred", "date": days("2026-03-01", "2026-04-30")}},
    {"type": "filter", "query": "escalated balance checks in March",
     "match": {"category": "account_balance"},
     "where": {"outcome": "escalated", "date": month("2026-03")}},

    # exact-phrase: a verbatim string from exactly one category's template (asserted
    # below), or an id spoken in the call. Lexical (FTS) search should win here.
    {"type": "exact", "query": "provisional credit", "match": {"category": "fraud_dispute"}},
    {"type": "exact", "query": "hardship program", "match": {"category": "payment_assistance"}},
    {"type": "exact", "query": "high-yield savings", "match": {"category": "account_opening"}},
    {"type": "exact", "query": "secure password reset link",
     "match": {"category": "online_banking_support"}},
    {"type": "exact", "query": "pre-qualification", "match": {"category": "loan_inquiry"}},
    {"type": "exact", "query": "loan-to-value", "match": {"category": "mortgage_inquiry"}},
    {"type": "exact", "id_kind": "member", "rank": 0},
    {"type": "exact", "id_kind": "member", "rank": 1},
    {"type": "exact", "id_kind": "account", "rank": 0},
    {"type": "exact", "id_kind": "account", "rank": 1},

    # paraphrase: the request avoids the target template's wording (asserted below:
    # at most one shared content word), so lexical search has little to match.
    {"type": "paraphrase", "query": "someone used my plastic without permission",
     "match": {"category": "fraud_dispute"}},
    {"type": "paraphrase", "query": "locked out of the web portal, forgot my login",
     "match": {"category": "online_banking_support"}},
    {"type": "paraphrase", "query": "money is tight and I need to postpone what I owe",
     "match": {"category": "payment_assistance"}},
    {"type": "paraphrase", "query": "how are my stocks and bonds doing",
     "match": {"category": "investment_review"}},
    {"type": "paraphrase", "query": "what am I protected against if I crash my car",
     "match": {"category": "insurance_service"}},
    {"type": "paraphrase", "query": "billed a service charge I believed did not apply",
     "match": {"category": "fee_dispute"}},
    {"type": "paraphrase", "query": "my impound reserve came up short after the county reassessment",
     "match": {"category": "escrow_analysis"}},
    {"type": "paraphrase", "query": "how much do I still owe on my house and what is it worth",
     "match": {"category": "mortgage_inquiry"}},
    {"type": "paraphrase", "query": "is my guaranteed borrowing price still held until closing",
     "match": {"category": "rate_lock_status"}},
]

# Function words ignored by the paraphrase overlap check (tokens as `tokens()` splits them,
# so "I'd" is "i" + "d"). Every other shared word counts.
STOPWORDS = frozenset([
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "d", "did", "do", "does",
    "for", "from", "has", "have", "how", "i", "if", "in", "is", "it", "its", "m", "me", "my",
    "no", "not", "of", "on", "or", "our", "s", "so", "that", "the", "their", "them", "then",
    "there", "this", "to", "up", "was", "we", "what", "when", "where", "which", "who", "will",
    "with", "would", "you", "your",
])


# ── generator facts ───────────────────────────────────────────────────────────

def load_templates() -> dict[str, list[tuple[str, str]]]:
    """CATEGORY_DIALOGUES from the generator (importing it runs no generation)."""
    path = DATA_DIR / "generate_data.py"
    spec = importlib.util.spec_from_file_location("_ccai_generate_data", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CATEGORY_DIALOGUES


def load_calls(data_dir: Path = DATA_DIR) -> list[dict]:
    """One fact row per call, joined from the generator's JSON outputs."""
    def read(name):
        path = data_dir / name
        if not path.exists():
            sys.exit(f"{path} not found. Run `make seed` first.")
        return json.loads(path.read_text())

    transcripts = read("transcripts.json")
    lob_by_product = {p["product_id"]: p["lob"] for p in read("products.json")}
    product_by_account = {a["account_id"]: a["product_id"] for a in read("accounts.json")}
    subject = {
        r["interaction_id"]: r["account_id"]
        for r in read("interaction_accounts.json")
        if r["account_role"] == "subject"
    }
    call_to_interaction = {r["call_id"]: r["interaction_id"] for r in read("interactions.json")}

    calls = []
    for t in transcripts:
        account = subject.get(call_to_interaction[t["call_id"]])
        calls.append({
            "call_id": t["call_id"],
            "category": t["category"],
            "outcome": t["outcome"],
            "date": t["date"],
            "member": t["member_id"],
            "account": account,
            "lob": lob_by_product[product_by_account[account]] if account else None,
        })
    return calls


def speaking_categories(templates: dict, placeholder: str) -> set[str]:
    """Categories whose dialogue template speaks `{placeholder}` aloud."""
    token = "{" + placeholder + "}"
    return {c for c, turns in templates.items() if any(token in text for _, text in turns)}


def template_text(templates: dict, category: str) -> str:
    return " ".join(text for _, text in templates[category])


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


# ── predicate evaluation ──────────────────────────────────────────────────────

def _holds(value, cond) -> bool:
    if isinstance(cond, dict):
        if "in" in cond:
            return value in cond["in"]
        return cond.get("gte", "") <= value <= cond.get("lte", "￿")
    return value == cond


def matches(call: dict, match: dict) -> bool:
    return all(_holds(call[k], cond) for k, cond in match.items())


def describe(match: dict) -> str:
    parts = []
    for k, cond in match.items():
        if isinstance(cond, dict) and "in" in cond:
            parts.append(f"{k} in {sorted(cond['in'])}")
        elif isinstance(cond, dict):
            parts.append(f"{cond['gte']} <= {k} <= {cond['lte']}")
        else:
            parts.append(f"{k} = {cond}")
    return " AND ".join(parts)


# ── id-based exact queries ────────────────────────────────────────────────────

def _id_spec(calls, templates, kind: str, rank: int) -> dict:
    """The `rank`-th member/account with the most calls that speak its id aloud.

    Ties break on the id, so the choice is deterministic. Accounts are spoken as
    their last four digits ("account ending in 0412")."""
    placeholder = "member_id" if kind == "member" else "account_last4"
    speaking = speaking_categories(templates, placeholder)
    counts = Counter(c[kind] for c in calls if c["category"] in speaking and c[kind] is not None)
    chosen = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[rank][0]
    match = {kind: chosen, "category": {"in": sorted(speaking)}}
    if kind == "member":
        return {"type": "exact", "query": chosen, "match": match}
    last4 = str(chosen).zfill(4)[-4:]
    return {"type": "exact", "query": f"account ending in {last4}", "match": match}


# ── build ─────────────────────────────────────────────────────────────────────

def build(calls: list[dict] | None = None, templates: dict | None = None) -> list[dict]:
    calls = calls if calls is not None else load_calls()
    templates = templates or load_templates()

    accounts = {c["account"] for c in calls if c["account"]}
    assert len({str(a).zfill(4)[-4:] for a in accounts}) == len(accounts), (
        "account last-4 digits are not unique, so 'account ending in NNNN' is ambiguous"
    )

    records = []
    for n, spec in enumerate(SPECS, start=1):
        if "id_kind" in spec:
            spec = _id_spec(calls, templates, spec["id_kind"], spec["rank"])
        where = spec.get("where")
        match = {**spec.get("match", {}), **(where or {})}
        relevant = sorted(c["call_id"] for c in calls if matches(c, match))
        assert len(relevant) >= 2, f"q{n:02d} {spec['query']!r}: only {len(relevant)} relevant"

        _check_wording(spec, templates)
        records.append({
            "id": f"q{n:02d}",
            "type": spec["type"],
            "query": spec["query"],
            "where": where,
            "relevant": relevant,
            "derivation": describe(match),
        })
    return records


def _check_wording(spec: dict, templates: dict) -> None:
    """Guard the query types' premises against the generator templates."""
    category = spec.get("match", {}).get("category")
    if spec["type"] == "exact" and isinstance(category, str):
        holders = [c for c in templates if spec["query"].lower() in template_text(templates, c).lower()]
        assert holders == [category], f"{spec['query']!r} is spoken by {holders}, not only {category}"
    if spec["type"] == "paraphrase":
        shared = (tokens(spec["query"]) - STOPWORDS) & tokens(template_text(templates, category))
        assert len(shared) <= 1, f"paraphrase {spec['query']!r} shares {sorted(shared)} with template"


def paraphrase_overlap(records: list[dict], templates: dict | None = None) -> dict[str, list[str]]:
    """Content words each paraphrase query shares with its target template (for the README)."""
    templates = templates or load_templates()
    out = {}
    for spec, rec in zip(SPECS, records):
        if rec["type"] == "paraphrase":
            text = template_text(templates, spec["match"]["category"])
            out[rec["id"]] = sorted((tokens(rec["query"]) - STOPWORDS) & tokens(text))
    return out


def dumps(records: list[dict]) -> str:
    return "".join(json.dumps(r, sort_keys=False) + "\n" for r in records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="fail if queries.jsonl differs from a fresh derivation")
    args = parser.parse_args(argv)

    text = dumps(build())
    if args.check:
        if not LABELS_PATH.exists() or LABELS_PATH.read_text() != text:
            print(f"{LABELS_PATH} is stale; run python -m retrieval.labels.build_labels")
            return 1
        print(f"{LABELS_PATH} is up to date")
        return 0
    LABELS_PATH.write_text(text)
    counts = Counter(json.loads(line)["type"] for line in text.splitlines())
    print(f"wrote {sum(counts.values())} queries to {LABELS_PATH}: {dict(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
