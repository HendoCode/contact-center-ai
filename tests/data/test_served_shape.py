"""Nothing the services read marks a call as an event call.

The answer key says which calls were planted. These tests check the served files and the vector
metadata for structure, not only for label strings: every row has the keys the schema had
before events existed plus the documented new columns, and no single scalar field splits the
event calls from the baseline calls.
"""

# The schema before events existed, per served file. Rows carry exactly these keys.
SCHEMA = {
    "account_owners": {"account_id", "member_id", "ownership_role"},
    "accounts": {"account_id", "account_number", "opened_by_staff_id", "opened_date",
                 "primary_member_id", "product_id", "status"},
    "auto_insurance_policies": {"account_id", "annual_premium", "coverage_code", "deductible",
                                "monthly_premium", "vehicle_year"},
    "banking_accounts": {"account_id", "apy", "available_balance", "ledger_balance",
                         "monthly_fee", "sub_type", "waiver_min_balance"},
    "credit_card_accounts": {"account_id", "available_credit", "cash_advance_apr", "credit_limit",
                             "outstanding_balance", "purchase_apr", "rewards_rate"},
    "csat": {"call_id", "category", "comment", "member_id", "score", "survey_date"},
    "home_equity_accounts": {"account_id", "base_rate", "credit_limit", "draw_period_months",
                             "drawn_balance", "margin"},
    "households": {"household_id", "household_label", "income_band"},
    "interaction_accounts": {"account_id", "account_role", "interaction_id"},
    "interaction_categories": {"category_code", "category_name", "lob_id"},
    "interactions": {"call_id", "caller_type", "category_code", "channel", "direction",
                     "duration_seconds", "ended_at", "first_contact_resolved", "interaction_id",
                     "member_id", "outcome", "staff_id", "started_at"},
    "investment_accounts": {"account_id", "advisory_fee_bps", "cash_value", "cost_basis",
                            "inv_type", "market_value", "ytd_return_pct"},
    "members": {"business_id", "credit_score_band", "email", "employee_count", "household_id",
                "joined_date", "legal_name", "member_id", "member_type", "naics_code", "phone",
                "residence_state"},
    "mortgage_accounts": {"account_id", "current_principal", "escrow_balance", "ltv_at_origination",
                          "monthly_pi", "note_rate", "original_principal", "property_state",
                          "term_months"},
    "product_rates": {"effective_date", "is_teaser", "product_id", "product_rate_id", "rate_type",
                      "rate_value"},
    "products": {"is_active", "lob", "product_code", "product_id", "product_name"},
    "rate_locks": {"expires_at", "loan_amount", "lock_id", "lock_period_days", "locked_at",
                   "locked_rate", "member_id", "product_id", "status"},
    "staff": {"full_name", "hire_date", "role", "staff_id", "team_id"},
    "teams": {"function_area", "team_id", "team_name"},
    "transactions": {"account_id", "amount", "description", "posted_at", "status",
                     "transaction_id", "txn_type"},
    "transcripts": {"agent_id", "call_id", "category", "date", "duration_seconds", "full_text",
                    "member_id", "member_name", "outcome", "transcript"},
}
# Files that are new, and columns added to old files: all documented in the data dictionary.
NEW_FILES = {"branches": {"name"}, "change_log": {"date", "name"}}
NEW_COLUMNS = {"members": {"home_branch"}, "transactions": {"fee_type"}}

METADATA_CORE = {"call_id", "date", "duration_seconds", "category", "outcome", "member_id",
                 "agent_id", "interaction_id"}
METADATA_OPTIONAL = {"account_id", "account_ids"}          # only calls about an account
METADATA_NEW = {"home_branch"}


def test_served_rows_carry_the_old_keys_plus_the_documented_columns(corpus):
    expected = {name: keys | NEW_COLUMNS.get(name, set()) for name, keys in SCHEMA.items()}
    expected.update(NEW_FILES)
    served = {p.stem for p in corpus.dir.glob("*.json")}
    assert served == set(expected)
    for name, keys in expected.items():
        for row in corpus.load(f"{name}.json"):
            assert set(row) == keys, (name, set(row) ^ keys)


def test_transcript_turns_carry_a_speaker_and_text_only(corpus):
    for t in corpus.transcripts.values():
        assert all(set(turn) == {"speaker", "text"} for turn in t["transcript"]), t["call_id"]


def test_vector_metadata_carries_the_old_keys_plus_home_branch(corpus):
    from rag import pipeline

    docs = pipeline.transcripts_to_documents(list(corpus.transcripts.values()), corpus.dir)
    for doc in docs:
        keys = set(doc.metadata)
        assert METADATA_CORE | METADATA_NEW <= keys <= METADATA_CORE | METADATA_NEW | METADATA_OPTIONAL
        assert ("account_id" in keys) == ("account_ids" in keys)


# ── no scalar field splits event calls from baseline calls ────────────────────

MAX_CATEGORIES = 60          # a field with more distinct values than this is an identifier


def call_scalars(corpus) -> dict[str, dict]:
    """Every single-valued field a service can read about a call, by call id."""
    from rag import pipeline

    members = {f"MBR-{m['member_id']:06d}": m for m in corpus.load("members.json")}
    surveys = {s["call_id"]: s for s in corpus.load("csat.json")}
    docs = {d.metadata["call_id"]: d.metadata
            for d in pipeline.transcripts_to_documents(list(corpus.transcripts.values()), corpus.dir)}
    skip = {"call_id", "interaction_id", "member_id", "started_at", "ended_at", "date",
            "survey_date", "full_text", "transcript", "comment", "member_name"}
    out = {}
    for cid, interaction in corpus.interactions.items():
        transcript = corpus.transcripts[cid]
        row = {f"interaction.{k}": v for k, v in interaction.items() if k not in skip}
        row.update({f"transcript.{k}": v for k, v in transcript.items() if k not in skip})
        row.update({f"metadata.{k}": v for k, v in docs[cid].items()
                    if k not in skip and not isinstance(v, list)})
        row["has_survey"] = cid in surveys
        if cid in surveys:
            row.update({f"survey.{k}": v for k, v in surveys[cid].items() if k not in skip})
        row["member.home_branch"] = members[transcript["member_id"]]["home_branch"]
        row["turns"] = len(transcript["transcript"])
        row["words"] = len(transcript["full_text"].split())
        out[cid] = row
    return out


def test_no_scalar_field_separates_event_calls_from_baseline_calls(corpus):
    scalars = call_scalars(corpus)
    event_ids = set(corpus.truth["calls"])
    assert event_ids and len(event_ids) < len(scalars)
    fields = {f for row in scalars.values() for f in row}
    assert len(fields) >= 12
    for field in sorted(fields):
        event = [r[field] for cid, r in scalars.items() if cid in event_ids and field in r]
        base = [r[field] for cid, r in scalars.items() if cid not in event_ids and field in r]
        if not event or not base:
            continue                     # a field only one side has is a tell by itself
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in event + base):
            assert not (max(event) < min(base) or min(event) > max(base)), field
        if len(set(event) | set(base)) <= MAX_CATEGORIES:
            assert set(event) & set(base), field


def test_event_and_baseline_calls_have_the_same_fields(corpus):
    scalars = call_scalars(corpus)
    event_ids = set(corpus.truth["calls"])
    event_fields = {f for cid, r in scalars.items() if cid in event_ids for f in r}
    base_fields = {f for cid, r in scalars.items() if cid not in event_ids for f in r}
    assert event_fields == base_fields


def test_every_ledger_row_posts_at_the_baseline_time_of_day(corpus):
    """Event ledger rows are not told apart by the time they post."""
    assert {r["posted_at"][11:] for r in corpus.load("transactions.json")} == {"12:00:00"}
