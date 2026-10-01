"""
OLTP seed loader: deterministic JSON → Postgres, idempotent.

Loads the JSON files produced by data/synthetic/generate_data.py into the OLTP
schema (olap/oltp/schema.sql). Every load is an UPSERT keyed on the table's
primary key, so re-running never duplicates rows (deliberately unlike the
vector ingest, which has no dedup).

Usage:
    python olap/seed.py
    DATABASE_URL=postgresql://postgres:postgres@localhost:5432/contactcenter \
        python olap/seed.py

Requires the schema to already be applied:  olap/oltp/apply.sh
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "synthetic"

DEFAULT_DB_URL = "postgresql://postgres:postgres@localhost:5432/contactcenter"


# ── JSON → Python type helpers ────────────────────────────────────────────────

def as_date(s):
    # csat.json survey_date is a full ISO datetime ('2026-06-08T00:00:00');
    # other date columns are plain dates ('2026-06-08').
    return date.fromisoformat(s[:10])


def as_ts(s):
    # naive ISO timestamps; Postgres stores them (session tz) as timestamptz
    return datetime.fromisoformat(s)


def call_to_int(call_id):
    """'CALL-00042' -> 42 (interaction_id)."""
    return int(call_id.rsplit("-", 1)[1])


def load_json(name):
    path = DATA_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run: python data/synthetic/generate_data.py")
    with open(path) as f:
        return json.load(f)


# ── upsert helpers ────────────────────────────────────────────────────────────

def upsert(cur, table, rows, columns, pk, page_size=1000):
    """UPSERT rows into table, keyed on pk columns. Returns count."""
    if not rows:
        return 0
    cols = list(columns)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s "
        f"ON CONFLICT ({', '.join(pk)}) DO UPDATE SET "
        + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in pk)
    )
    records = [tuple(r[c] for c in cols) for r in rows]
    execute_values(cur, sql, records, page_size=page_size)
    return len(rows)


def table_count(cur, table):
    cur.execute(f"SELECT count(*) FROM {table}")
    return cur.fetchone()[0]


# ── per-table preparers (JSON row → row typed for Postgres) ──────────────────

def prep_households(rows):
    for r in rows:
        yield {"household_id": r["household_id"], "household_label": r["household_label"],
               "income_band": r["income_band"]}


def prep_members(rows):
    for r in rows:
        yield {
            "member_id": int(r["member_id"]),
            "household_id": r.get("household_id"),
            "member_type": r["member_type"],
            "legal_name": r["legal_name"],
            "email": r.get("email"),
            "phone": r.get("phone"),
            "joined_date": as_date(r["joined_date"]),
            "credit_score_band": r.get("credit_score_band"),
            "residence_state": r.get("residence_state"),
            "business_id": r.get("business_id"),
            "naics_code": r.get("naics_code"),
            "employee_count": r.get("employee_count"),
        }


def prep_teams(rows):
    for r in rows:
        yield {"team_id": int(r["team_id"]), "team_name": r["team_name"],
               "function_area": r["function_area"]}


def prep_staff(rows):
    for r in rows:
        yield {"staff_id": int(r["staff_id"]), "full_name": r["full_name"],
               "team_id": int(r["team_id"]), "role": r["role"],
               "hire_date": as_date(r["hire_date"])}


def prep_products(rows):
    for r in rows:
        yield {"product_id": int(r["product_id"]), "lob": r["lob"],
               "product_code": r["product_code"], "product_name": r["product_name"],
               "is_active": bool(r["is_active"])}


def prep_accounts(rows):
    for r in rows:
        yield {"account_id": int(r["account_id"]), "product_id": int(r["product_id"]),
               "primary_member_id": int(r["primary_member_id"]),
               "account_number": r["account_number"],
               "opened_date": as_date(r["opened_date"]),
               "status": r["status"],
               "opened_by_staff_id": r.get("opened_by_staff_id")}


def prep_account_owners(rows):
    for r in rows:
        yield {"account_id": int(r["account_id"]), "member_id": int(r["member_id"]),
               "ownership_role": r["ownership_role"]}


def _passthrough_child(r):
    out = {"account_id": int(r["account_id"])}
    for k, v in r.items():
        if k != "account_id":
            out[k] = v
    return out


def prep_child(rows):
    for r in rows:
        yield _passthrough_child(r)


def prep_product_rates(rows):
    for r in rows:
        yield {"product_rate_id": int(r["product_rate_id"]),
               "product_id": int(r["product_id"]),
               "effective_date": as_date(r["effective_date"]),
               "rate_type": r["rate_type"], "rate_value": r["rate_value"],
               "is_teaser": bool(r["is_teaser"])}


def prep_rate_locks(rows):
    for r in rows:
        yield {"lock_id": int(r["lock_id"]), "member_id": int(r["member_id"]),
               "product_id": int(r["product_id"]), "locked_rate": r["locked_rate"],
               "loan_amount": r["loan_amount"], "locked_at": as_ts(r["locked_at"]),
               "expires_at": as_ts(r["expires_at"]),
               "lock_period_days": int(r["lock_period_days"]), "status": r["status"]}


def prep_interaction_categories(rows):
    for r in rows:
        yield {"category_code": r["category_code"], "category_name": r["category_name"],
               "lob_id": r.get("lob_id")}


def prep_interactions(rows):
    for r in rows:
        yield {"interaction_id": int(r["interaction_id"]),
               "member_id": int(r["member_id"]), "staff_id": int(r["staff_id"]),
               "category_code": r["category_code"],
               "started_at": as_ts(r["started_at"]), "ended_at": as_ts(r["ended_at"]),
               "duration_seconds": int(r["duration_seconds"]),
               "channel": r["channel"], "direction": r["direction"],
               "caller_type": r["caller_type"], "outcome": r["outcome"],
               "first_contact_resolved": bool(r["first_contact_resolved"])}


def prep_interaction_accounts(rows):
    for r in rows:
        yield {"interaction_id": int(r["interaction_id"]),
               "account_id": int(r["account_id"]), "account_role": r["account_role"]}


def prep_interaction_transcripts(rows):
    """Derived from transcripts.json (keeps the exact RAG-facing shape)."""
    for t in rows:
        utterances = t["transcript"]
        yield {"interaction_id": call_to_int(t["call_id"]),
               "full_text": t["full_text"],
               "utterances": Json(utterances),
               "turn_count": len(utterances)}


def prep_csat_surveys(rows):
    """Derived from csat.json.

    survey_id is set equal to interaction_id (each interaction has at most one
    survey, enforced by the UNIQUE constraint), so this upsert stays stable even
    when the data is regenerated between runs.
    """
    for c in rows:
        iid = call_to_int(c["call_id"])
        yield {"survey_id": iid,
               "interaction_id": iid,
               "score": int(c["score"]),
               "comment": c["comment"],
               "surveyed_at": as_date(c["survey_date"]),
               "question_code": "overall"}


def prep_transactions(rows):
    for r in rows:
        yield {"transaction_id": int(r["transaction_id"]),
               "account_id": int(r["account_id"]),
               "posted_at": as_ts(r["posted_at"]),
               "amount": r["amount"], "txn_type": r["txn_type"],
               "status": r["status"], "description": r["description"]}


# ── table registry: (table, columns, pk, preparer, source_json) ───────────────

TABLES = [
    ("household", ["household_id", "household_label", "income_band"],
     ["household_id"], prep_households, "households.json"),
    ("member", ["member_id", "household_id", "member_type", "legal_name", "email",
                "phone", "joined_date", "credit_score_band", "residence_state",
                "business_id", "naics_code", "employee_count"],
     ["member_id"], prep_members, "members.json"),
    ("team", ["team_id", "team_name", "function_area"], ["team_id"],
     prep_teams, "teams.json"),
    ("staff", ["staff_id", "full_name", "team_id", "role", "hire_date"],
     ["staff_id"], prep_staff, "staff.json"),
    ("product", ["product_id", "lob", "product_code", "product_name", "is_active"],
     ["product_id"], prep_products, "products.json"),
    ("account", ["account_id", "product_id", "primary_member_id", "account_number",
                 "opened_date", "status", "opened_by_staff_id"],
     ["account_id"], prep_accounts, "accounts.json"),
    ("account_owner", ["account_id", "member_id", "ownership_role"],
     ["account_id", "member_id"], prep_account_owners, "account_owners.json"),

    # LOB child tables (Table-Per-Type)
    ("mortgage_account",
     ["account_id", "original_principal", "current_principal", "note_rate",
      "term_months", "monthly_pi", "escrow_balance", "ltv_at_origination",
      "property_state"],
     ["account_id"], prep_child, "mortgage_accounts.json"),
    ("home_equity_account",
     ["account_id", "credit_limit", "drawn_balance", "base_rate", "margin",
      "draw_period_months"],
     ["account_id"], prep_child, "home_equity_accounts.json"),
    ("auto_insurance_policy",
     ["account_id", "annual_premium", "monthly_premium", "coverage_code",
      "deductible", "vehicle_year"],
     ["account_id"], prep_child, "auto_insurance_policies.json"),
    ("banking_account",
     ["account_id", "sub_type", "ledger_balance", "available_balance", "apy",
      "monthly_fee", "waiver_min_balance"],
     ["account_id"], prep_child, "banking_accounts.json"),
    ("credit_card_account",
     ["account_id", "credit_limit", "outstanding_balance", "available_credit",
      "purchase_apr", "cash_advance_apr", "rewards_rate"],
     ["account_id"], prep_child, "credit_card_accounts.json"),
    ("investment_account",
     ["account_id", "inv_type", "market_value", "cost_basis", "cash_value",
      "ytd_return_pct", "advisory_fee_bps"],
     ["account_id"], prep_child, "investment_accounts.json"),

    ("product_rate", ["product_rate_id", "product_id", "effective_date",
                      "rate_type", "rate_value", "is_teaser"],
     ["product_rate_id"], prep_product_rates, "product_rates.json"),
    ("rate_lock", ["lock_id", "member_id", "product_id", "locked_rate",
                   "loan_amount", "locked_at", "expires_at", "lock_period_days",
                   "status"],
     ["lock_id"], prep_rate_locks, "rate_locks.json"),
    ("interaction_category", ["category_code", "category_name", "lob_id"],
     ["category_code"], prep_interaction_categories, "interaction_categories.json"),
    ("interaction", ["interaction_id", "member_id", "staff_id", "category_code",
                     "started_at", "ended_at", "duration_seconds", "channel",
                     "direction", "caller_type", "outcome", "first_contact_resolved"],
     ["interaction_id"], prep_interactions, "interactions.json"),
    ("interaction_account", ["interaction_id", "account_id", "account_role"],
     ["interaction_id", "account_id"], prep_interaction_accounts,
     "interaction_accounts.json"),
    ("interaction_transcript", ["interaction_id", "full_text", "utterances",
                                "turn_count"],
     ["interaction_id"], prep_interaction_transcripts, "transcripts.json"),
    ("csat_survey", ["survey_id", "interaction_id", "score", "comment",
                     "surveyed_at", "question_code"],
     ["survey_id"], prep_csat_surveys, "csat.json"),
    ("account_transaction", ["transaction_id", "account_id", "posted_at",
                             "amount", "txn_type", "status", "description"],
     ["transaction_id"], prep_transactions, "transactions.json"),
]


def main():
    db_url = os.getenv("DATABASE_URL", DEFAULT_DB_URL)
    conn = psycopg2.connect(db_url)
    try:
        with conn, conn.cursor() as cur:
            for table, columns, pk, preparer, source in TABLES:
                rows = list(preparer(load_json(source)))
                n = upsert(cur, table, rows, columns, pk)
                print(f"  {table:<24} {n:>7} rows")

        print("\nPost-load row counts (Postgres):")
        with conn.cursor() as cur:
            for table in [
                "household", "member", "team", "staff", "product", "account",
                "account_owner", "mortgage_account", "home_equity_account",
                "auto_insurance_policy", "banking_account", "credit_card_account",
                "investment_account", "product_rate", "rate_lock",
                "interaction_category", "interaction", "interaction_account",
                "interaction_transcript", "csat_survey", "account_transaction",
            ]:
                print(f"  {table:<24} {table_count(cur, table):>7}")
    finally:
        conn.close()

    print("\nSeed complete. Re-run to verify idempotency (counts unchanged).")


if __name__ == "__main__":
    sys.exit(main())
