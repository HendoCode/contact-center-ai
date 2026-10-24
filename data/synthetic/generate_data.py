"""
Synthetic data generator for contact-center-ai portfolio project.

Generates realistic (but entirely fake) call-center and OLTP data for
"Meridian Valley Credit Union" (MVCU), the fictional CU:

  * The two RAG/MCP outputs, with the same key shape as ever:
      - data/synthetic/transcripts.json   (1,250 baseline calls plus event calls)
      - data/synthetic/csat.json          (surveys for ~75% of those calls)
  * The OLTP foundation JSON files (one per table) loaded by olap/seed.py:
      households, members, teams, staff, products, accounts (+6 LOB child
      table files), account_owners, product_rates, rate_locks,
      interaction_categories, interactions, interaction_accounts, transactions.
  * branches.json and change_log.json (names and dates only), which the services may read.
  * ground_truth/events.json, the answer key for the planted events. Only evaluators read it.

Dated events (events.yml): each event adds calls on top of the baseline for a few weeks,
with a driver mix and a CSAT shift, so "why did this number move?" has a known answer. Event
calls come from their own seeded streams; the baseline stream is untouched, so a run with
no events reproduces the baseline calls exactly. All ids are then renumbered in time order,
so no id marks an event.

Determinism: a single seeded RNG (random.seed(42)) plus Faker.seed(42), used in
a strictly fixed order, so TWO RUNS ARE BIT-IDENTICAL. Members are persistent
identities (the old generator re-rolled them every call). Event calls and dialogue wording
use two more seeded streams, and branch assignment hashes the member id with SHA-256.

The transcripts are *numerically bound to accounts*: dialogue templates splice
the caller's real note_rate / ledger_balance / purchase_apr / apy / etc. so the
RAG view (transcripts.json) and the OLAP view (Postgres) agree on the same
number from different engines.

Usage:
    python data/synthetic/generate_data.py
    python data/synthetic/generate_data.py --output-dir DIR --events events.yml
"""

import argparse
import hashlib
import json
import random
import sys
import uuid
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from pathlib import Path

import yaml
from faker import Faker

# dialogue_templates.py sits next to this script; put its folder on the path so the
# import also works when other tools load this file by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dialogue_templates import CATEGORY_DIALOGUES, N_VARIANTS, compose_event_turns  # noqa: E402

# ── Seeds / determinism anchors ───────────────────────────────────────────────

SEED = 42
random.seed(SEED)
faker = Faker()
Faker.seed(SEED)
faker.seed_instance(SEED)

# Fixed "today" so two runs are bit-identical regardless of when they run.
# The old generator used datetime.now(), which broke cross-run determinism.
REFERENCE_DATE = datetime(2026, 9, 1, 12, 0, 0)

# ── Domain constants ──────────────────────────────────────────────────────────

LOBS = ["mortgage", "home", "car_insurance", "banking", "cards", "investments"]

# 14 categories (extends the original 8), with the §3 mix weights.
CATEGORY_WEIGHTS = [
    ("account_balance", 16),
    ("online_banking_support", 12),
    ("card_services", 10),
    ("fraud_dispute", 9),
    ("payment_assistance", 9),
    ("rate_inquiry", 8),
    ("loan_inquiry", 8),
    ("mortgage_inquiry", 7),
    ("insurance_service", 6),
    ("investment_review", 5),
    ("account_opening", 5),
    ("fee_dispute", 3),
    ("escrow_analysis", 1),
    ("rate_lock_status", 1),
]
CATEGORIES = [c for c, _ in CATEGORY_WEIGHTS]
CATEGORY_W = [w for _, w in CATEGORY_WEIGHTS]

# A loose "this category usually concerns these LOBs" map used to pick a
# caller who actually *has* such an account. Empty list = any member works.
CATEGORY_LOBS = {
    "account_balance": ["banking", "cards"],
    "online_banking_support": ["banking"],
    "card_services": ["cards"],
    "fraud_dispute": ["cards", "banking"],
    "payment_assistance": ["mortgage", "home", "cards"],
    "rate_inquiry": ["mortgage", "home", "cards", "banking"],
    "loan_inquiry": ["mortgage", "home"],
    "mortgage_inquiry": ["mortgage"],
    "insurance_service": ["car_insurance"],
    "investment_review": ["investments"],
    "account_opening": [],
    "fee_dispute": ["banking"],
    "escrow_analysis": ["mortgage"],
    "rate_lock_status": ["mortgage", "home"],
}

OUTCOMES = ["resolved", "escalated", "callback_scheduled", "unresolved", "transferred"]
CHANNELS = ["phone", "chat", "secure_message", "branch"]
DIRECTIONS = ["inbound", "outbound", "callback"]

INCOME_BANDS = ["<$50k", "$50-100k", "$100-200k", ">$200k"]
CREDIT_BANDS = ["excellent", "good", "fair", "poor"]

# Posted APY of the flagship High-Yield Savings product (product #13), captured
# during product_rates generation and spliced into "account_opening" dialogue.
HIGH_YIELD_APY = 5.0

US_STATES = [
    "WA", "OR", "CA", "ID", "NV", "AZ", "UT", "CO", "MT", "WY",
    "NM", "TX", "OK", "KS", "NE", "SD", "ND", "MN", "IA", "MO",
    "AR", "LA", "MS", "AL", "GA", "FL", "SC", "NC", "TN", "KY",
    "OH", "IN", "IL", "WI", "MI", "PA", "NY", "ME", "VT", "MA",
]

# ── Count plan ────────────────────────────────────────────────────────────────

N_HOUSEHOLDS = 150
N_INDIVIDUALS = 550
N_BUSINESSES = 40
N_CONTACTS = 110           # individual-type rows linked to a business (self-FK)
N_MEMBERS = N_INDIVIDUALS + N_BUSINESSES + N_CONTACTS   # 700

N_TEAMS = 8
N_STAFF = 60

# products: 3-5 per LOB (24 total)
PRODUCT_PLAN = [
    ("mortgage", ["MVG-30F", "MVG-15F", "MVG-51A", "MVG-J30"],
     ["30-Year Fixed Mortgage", "15-Year Fixed Mortgage", "5/1 ARM Mortgage", "Jumbo 30-Year Mortgage"]),
    ("home", ["HEQ-PRM", "HEQ-STR", "HEQ-FXD"],
     ["Prime HELOC", "Starter HELOC", "Fixed Home Equity Loan"]),
    ("car_insurance", ["INS-FULL", "INS-LIAB", "INS-LC", "INS-TEEN"],
     ["Full Coverage Auto", "Liability Only Auto", "Liability + Collision Auto", "Young Driver Auto"]),
    ("banking", ["BNK-CHK", "BNK-HYS", "BNK-MM", "BNK-CD12"],
     ["Free Checking", "High-Yield Savings", "Money Market", "12-Month Certificate"]),
    ("cards", ["CRD-PLT", "CRD-RWD", "CRD-SEC", "CRD-BIZ", "CRD-STU"],
     ["Platinum Visa", "Rewards Visa", "Secured Visa", "Business Visa", "Student Visa"]),
    ("investments", ["INV-BRK", "INV-IRA", "INV-ROTH", "INV-MGD"],
     ["Self-Directed Brokerage", "Traditional IRA", "Roth IRA", "Managed Advisory"]),
]

# Account counts per LOB (sums to 1,400), split individual vs business owners.
# Individuals avg ~2.3 accounts; businesses avg exactly 4.
ACCOUNT_LOSS = {
    "banking":      {"individual": 490, "business": 40},   # 530
    "cards":        {"individual": 320, "business": 20},   # 340
    "mortgage":     {"individual": 150, "business": 20},   # 170
    "home":         {"individual": 90,  "business": 20},   # 110
    "car_insurance": {"individual": 140, "business": 0},   # 140
    "investments":  {"individual": 50,  "business": 60},   # 110
}                                                        # total 1,400

N_ACCOUNTS = sum(v["individual"] + v["business"] for v in ACCOUNT_LOSS.values())

N_INTERACTIONS = 1250
INTERACTION_MULTI_RATIO = 0.20   # 80/20 single/multi-account (§spec)
CSAT_RESPONSE_RATE = 0.75

# ~25,000 transactions across the 1,400 accounts.
TXNS_PER_ACCOUNT = {
    "banking": 21, "cards": 19, "mortgage": 7, "home": 9,
    "car_insurance": 13, "investments": 31,
}

N_RATE_LOCKS = 500

# ── helpers ───────────────────────────────────────────────────────────────────

def money(x):
    return f"${x:,.2f}" if x is not None else "$0.00"


def pct(x, digits=3):
    return f"{x:.{digits}f}%" if x is not None else "0.000%"


def mbr_str(member_id):
    return f"MBR-{member_id:06d}"


def agt_str(staff_id):
    return f"AGT-{staff_id:03d}"


def call_str(interaction_id):
    return f"CALL-{interaction_id:05d}"


def uuid4_deterministic():
    return str(uuid.UUID(int=random.getrandbits(128)))


def write_json(output_dir, name, data):
    with open(output_dir / name, "w") as f:
        json.dump(data, f, indent=2)


def rand_date_between(start, end):
    span = (end - start).days
    return start + timedelta(days=random.randint(0, span))


# ── 1. households ─────────────────────────────────────────────────────────────

def generate_households():
    rows = []
    for i in range(N_HOUSEHOLDS):
        band = random.choices(INCOME_BANDS, weights=[10, 35, 40, 15])[0]
        suffix = random.randint(1000, 9999)
        rows.append({
            "household_id": uuid4_deterministic(),
            "household_label": f"Household {suffix}",
            "income_band": band,
        })
    return rows


# ── 2. members ────────────────────────────────────────────────────────────────

def generate_members():
    rows = []
    # individuals 1..550, businesses 551..590, contacts 591..700
    for m in range(1, N_INDIVIDUALS + 1):
        hh = ((m - 1) % N_HOUSEHOLDS) + 1   # ~3.7 members per household
        rows.append(_member(m, "individual", household=hh,
                            credit=True, business_id=None))
    for m in range(N_INDIVIDUALS + 1, N_INDIVIDUALS + N_BUSINESSES + 1):
        rows.append(_member(m, "business", household=None,
                            credit=False, business_id=None))
    # business contacts: individual-type, linked to a business via business_id
    for m in range(N_INDIVIDUALS + N_BUSINESSES + 1, N_MEMBERS + 1):
        biz = random.randint(N_INDIVIDUALS + 1, N_INDIVIDUALS + N_BUSINESSES)
        rows.append(_member(m, "individual", household=None,
                            credit=True, business_id=biz))
    return rows


def _member(m, member_type, household, credit, business_id):
    name = faker.name()
    joined = rand_date_between(REFERENCE_DATE - timedelta(days=7300),
                               REFERENCE_DATE - timedelta(days=60))
    row = {
        "member_id": m,
        "household_id": None,
        "member_type": member_type,
        "legal_name": name,
        "email": None,
        "phone": None,
        "joined_date": joined.date().isoformat(),
        "credit_score_band": None,
        "residence_state": random.choice(US_STATES),
        "business_id": business_id,
        "naics_code": None,
        "employee_count": None,
        "home_branch": assign_home_branch(m),
    }
    if household is not None:
        row["household_id"] = households[household - 1]["household_id"]
    if credit:
        row["credit_score_band"] = random.choices(
            CREDIT_BANDS, weights=[25, 35, 25, 15])[0]
    if member_type == "business":
        row["naics_code"] = random.choice(["5221", "5312", "6211", "7225", "2361"])
        row["employee_count"] = random.randint(2, 250)
    if row["email"] is None:
        row["email"] = faker.email()
    if row["phone"] is None:
        row["phone"] = faker.phone_number()
    return row


# ── 3. teams + staff ──────────────────────────────────────────────────────────

def generate_teams():
    areas = ["servicing", "loans", "cards", "fraud", "insurance", "investments",
             "servicing", "loans"]
    rows = []
    for i, area in enumerate(areas, start=1):
        rows.append({
            "team_id": i,
            "team_name": f"{area.title()} Team",
            "function_area": area,
        })
    return rows


def generate_staff():
    rows = []
    team_pool = list(range(1, N_TEAMS + 1)) * 10
    for s in range(1, N_STAFF + 1):
        role = random.choices(["agent", "specialist", "supervisor"],
                              weights=[70, 20, 10])[0]
        rows.append({
            "staff_id": s,
            "full_name": faker.name(),
            "team_id": team_pool[s % len(team_pool)],
            "role": role,
            "hire_date": rand_date_between(
                REFERENCE_DATE - timedelta(days=5475),
                REFERENCE_DATE - timedelta(days=30)).date().isoformat(),
        })
    return rows


# ── 4. products ───────────────────────────────────────────────────────────────

def generate_products():
    rows = []
    pid = 1
    for lob, codes, names in PRODUCT_PLAN:
        for code, name in zip(codes, names):
            rows.append({
                "product_id": pid,
                "lob": lob,
                "product_code": code,
                "product_name": name,
                "is_active": True,
            })
            pid += 1
    return rows


# ── 5. accounts + LOB children + owners ───────────────────────────────────────

def generate_accounts():
    """Assign each member their accounts deterministically, then emit child rows.

    Returns (accounts_core, child_rows_by_lob, owners, accounts_index) where
    accounts_index maps member_id -> [account_id, ...] for later interactions.
    """
    core = []
    children = {lob: [] for lob in LOBS}
    owners = []
    member_accounts = {m: [] for m in range(1, N_MEMBERS + 1)}

    next_id = 1

    ind_members = list(range(1, N_INDIVIDUALS + 1))
    biz_members = list(range(N_INDIVIDUALS + 1, N_INDIVIDUALS + N_BUSINESSES + 1))

    # Deterministic per-member account plan: banking first (spread across
    # individuals so most have at least one), then the other LOBs round-robin.
    allocations = []  # (member_id, lob)
    banking_ind = ACCOUNT_LOSS["banking"]["individual"]
    for i, m in enumerate(ind_members):
        if i < banking_ind:
            allocations.append((m, "banking"))
    for m in biz_members:
        allocations.append((m, "banking"))
    for lob in LOBS:
        if lob == "banking":
            continue
        ind_n = ACCOUNT_LOSS[lob]["individual"]
        biz_n = ACCOUNT_LOSS[lob]["business"]
        for k in range(ind_n):
            allocations.append((ind_members[k % N_INDIVIDUALS], lob))
        for k in range(biz_n):
            allocations.append((biz_members[k % N_BUSINESSES], lob))

    for (member_id, lob) in allocations:
        account_id = next_id
        next_id += 1
        child = _child_row(lob, account_id, member_id)
        if lob == "banking":
            # banking sub_type determines the concrete product (checking →
            # Free Checking, certificate → 12-mo Certificate, ...)
            product = product_by_id(BANKING_PRODUCT_BY_SUB[child["sub_type"]])
        else:
            product = pick_product(lob, member_id)
        opened = rand_date_between(
            datetime(REFERENCE_DATE.year - 15, 1, 1), REFERENCE_DATE - timedelta(days=30))
        core.append({
            "account_id": account_id,
            "product_id": product["product_id"],
            "primary_member_id": member_id,
            "account_number": f"****{account_id:04d}",
            "opened_date": opened.date().isoformat(),
            "status": "open",
            "opened_by_staff_id": random.randint(1, N_STAFF),
        })
        member_accounts[member_id].append(account_id)
        owners.append({"account_id": account_id, "member_id": member_id,
                       "ownership_role": "primary"})
        children[lob].append(child)

    # Joint / authorized-user noise: ~10% of individual accounts get a second
    # owner, seeded deterministically. This is the "double-count" trap.
    for account_id in list(range(1, next_id)):
        if random.random() < 0.10:
            acct = core[account_id - 1]
            # second owner: prefer a household-mate if the primary has one,
            # else a random individual other than the primary.
            extra = random.choice([m for m in ind_members
                                   if m != acct["primary_member_id"]])
            owners.append({
                "account_id": account_id,
                "member_id": extra,
                "ownership_role": random.choice(["joint", "authorized_user"]),
            })

    return core, children, owners, member_accounts, next_id - 1


def pick_product(lob, member_id):
    lob_products = [p for p in products if p["lob"] == lob]
    return random.choice(lob_products)


def product_by_id(pid):
    return next(p for p in products if p["product_id"] == pid)


BANKING_PRODUCT_BY_SUB = {"checking": 12, "savings": 13,
                          "money_market": 14, "certificate": 15}


def _child_row(lob, account_id, member_id):
    if lob == "banking":
        sub = random.choices(list(BANKING_PRODUCT_BY_SUB.keys()),
                             weights=[55, 25, 12, 8])[0]
        ledger = {
            "checking": random.uniform(500, 25000),
            "savings": random.uniform(1000, 200000),
            "money_market": random.uniform(5000, 500000),
            "certificate": random.uniform(1000, 100000),
        }[sub]
        ledger = round(ledger, 2)
        available = round(max(0.0, ledger - random.uniform(0, 500)), 2)
        apy = round(random.uniform(3.5, 5.25), 3) if sub != "checking" else 0.0
        return {
            "account_id": account_id,
            "sub_type": sub,
            "ledger_balance": ledger,
            "available_balance": available,
            "apy": apy,
            "monthly_fee": round(random.uniform(0, 12), 2),
            "waiver_min_balance": round(random.uniform(0, 5000), 2),
        }
    if lob == "cards":
        limit = round(random.uniform(500, 30000), 2)
        outstanding = round(limit * random.uniform(0.05, 0.85), 2)
        # teaser posture: ~15% of cards have a 0% intro purchase APR
        teaser = random.random() < 0.15
        purchase = 0.0 if teaser else round(random.uniform(15.99, 27.99), 3)
        cash = round(random.uniform(23.99, 29.99), 3) if teaser else \
            round(purchase + random.uniform(0.0, 4.0), 3)
        return {
            "account_id": account_id,
            "credit_limit": limit,
            "outstanding_balance": outstanding,
            "available_credit": round(max(0.0, limit - outstanding), 2),
            "purchase_apr": purchase,
            "cash_advance_apr": cash,
            "rewards_rate": round(random.uniform(0, 2), 2),
        }
    if lob == "mortgage":
        original = round(random.uniform(150000, 800000), 2)
        current = round(original * random.uniform(0.55, 0.99), 2)
        note = round(random.uniform(5.5, 7.5), 3)
        return {
            "account_id": account_id,
            "original_principal": original,
            "current_principal": current,
            "note_rate": note,
            "term_months": random.choice([180, 360]),
            "monthly_pi": round(random.uniform(900, 4500), 2),
            "escrow_balance": round(random.uniform(0, 8000), 2),
            "ltv_at_origination": round(random.uniform(60, 95), 2),
            "property_state": random.choice(US_STATES),
        }
    if lob == "home":
        limit = round(random.uniform(20000, 200000), 2)
        return {
            "account_id": account_id,
            "credit_limit": limit,
            "drawn_balance": round(limit * random.uniform(0.1, 0.9), 2),
            "base_rate": round(random.uniform(7.5, 9.5), 3),
            "margin": round(random.uniform(0.5, 2.5), 3),
            "draw_period_months": 120,
        }
    if lob == "car_insurance":
        annual = round(random.uniform(800, 3600), 2)
        return {
            "account_id": account_id,
            "annual_premium": annual,
            "monthly_premium": round(annual / 12, 2),
            "coverage_code": random.choice(["liability", "full", "liab+coll"]),
            "deductible": round(random.uniform(250, 2000), 2),
            "vehicle_year": random.randint(2010, 2025),
        }
    if lob == "investments":
        inv_type = random.choices(["brokerage", "ira", "managed"],
                                  weights=[45, 35, 20])[0]
        market = round(random.uniform(5000, 500000), 2)
        return {
            "account_id": account_id,
            "inv_type": inv_type,
            "market_value": market,
            "cost_basis": round(market * random.uniform(0.55, 0.9), 2),
            "cash_value": round(market * random.uniform(0, 0.3), 2),
            "ytd_return_pct": round(random.uniform(-15.0, 25.0), 4),
            "advisory_fee_bps": random.randint(25, 100) if inv_type == "managed" else 0,
        }
    raise ValueError(f"unknown lob {lob}")


# ── 6. product_rates ──────────────────────────────────────────────────────────

def generate_product_rates():
    global HIGH_YIELD_APY
    rows = []
    rid = 1
    for p in products:
        lob = p["lob"]
        if lob in ("car_insurance", "investments"):
            continue  # no posted "rate" product for these LOBs
        rate_types = {
            "mortgage": ["note"],
            "home": ["base"],
            "banking": ["apy"],
            "cards": ["purchase_apr", "cash_advance_apr"],
        }[lob]
        for rt in rate_types:
            for k in range(6):
                base_value = {
                    ("mortgage", "note"): random.uniform(5.5, 7.5),
                    ("home", "base"): random.uniform(7.5, 9.5),
                    ("banking", "apy"): random.uniform(3.5, 5.25),
                    ("cards", "purchase_apr"): random.uniform(15.99, 27.99),
                    ("cards", "cash_advance_apr"): random.uniform(23.99, 29.99),
                }[(lob, rt)]
                value = round(base_value, 4)
                rows.append({
                    "product_rate_id": rid,
                    "product_id": p["product_id"],
                    "effective_date": (REFERENCE_DATE - timedelta(days=30 * (6 - k))).date().isoformat(),
                    "rate_type": rt,
                    "rate_value": value,
                    "is_teaser": (k == 0 and rt == "purchase_apr" and random.random() < 0.4),
                })
                # capture the newest APY for High-Yield Savings (product #13)
                if p["product_id"] == 13 and rt == "apy" and k == 5:
                    HIGH_YIELD_APY = value
                rid += 1
    return rows


# ── 7. rate_locks ─────────────────────────────────────────────────────────────

def generate_rate_locks(member_accounts):
    rows = []
    # eligible = members with a mortgage or home account
    eligible = [m for m, accts in member_accounts.items()
                if any(accounts[a]["lob"] in ("mortgage", "home") for a in accts)]
    for i in range(N_RATE_LOCKS):
        member_id = random.choice(eligible)
        accts = [a for a in member_accounts[member_id]
                 if accounts[a]["lob"] in ("mortgage", "home")]
        if not accts:
            continue
        account_id = random.choice(accts)
        product_id = accounts[account_id]["product_id"]
        locked_at = REFERENCE_DATE - timedelta(days=random.randint(0, 180))
        period = random.choice([30, 45, 60])
        expires_at = locked_at + timedelta(days=period)
        if expires_at < REFERENCE_DATE:
            status = random.choices(["exercised", "expired", "cancelled"],
                                    weights=[40, 50, 10])[0]
        else:
            status = "active"
        rows.append({
            "lock_id": i + 1,
            "member_id": member_id,
            "product_id": product_id,
            "locked_rate": round(random.uniform(5.5, 9.0), 3),
            "loan_amount": round(random.uniform(100000, 700000), 2),
            "locked_at": locked_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "lock_period_days": period,
            "status": status,
        })
    return rows


# ── 8. interaction categories ─────────────────────────────────────────────────

def generate_interaction_categories():
    # lob_id is a loose informational link (index into LOBS, 1-based),
    # NOT a hard FK — a card call can be a fraud call.
    lob_hints = {
        "mortgage_inquiry": "mortgage", "loan_inquiry": "mortgage",
        "escrow_analysis": "mortgage", "rate_lock_status": "mortgage",
        "card_services": "cards", "insurance_service": "car_insurance",
        "investment_review": "investments",
    }
    rows = []
    for code in CATEGORIES:
        lob = lob_hints.get(code)
        rows.append({
            "category_code": code,
            "category_name": code.replace("_", " ").title(),
            "lob_id": (LOBS.index(lob) + 1) if lob else None,
        })
    return rows


# ── 9. interactions + transcripts + junction + csat ───────────────────────────

def make_facts(member, subject, refs):
    """Build the splice dict for dialogue templates.

    subject = the account dict (or None); refs = list of account dicts.
    Values are pulled from the accounts' *real* child rows so transcript text
    and the OLTP rows agree.
    """
    facts = {
        "member_name": member["legal_name"],
        "member_id": mbr_str(member["member_id"]),
        "account_last4": "0000",
        # lob-agnostic generic facts (filled per subject LOB below)
        "balance_kind": "balance", "balance": "$0.00",
        "secondary_kind": "available", "secondary_balance": "$0.00",
        "rate_label": "rate", "rate": "0.000%",
        "payment_label": "payment", "payment_amount": "$0.00",
        "new_account_apy": pct(HIGH_YIELD_APY),
        "ledger_balance": "$0.00", "available_balance": "$0.00", "apy": "0.000%",
        "credit_limit": "$0.00", "outstanding_balance": "$0.00",
        "available_credit": "$0.00", "purchase_apr": "0.000%",
        "cash_advance_apr": "0.000%", "rewards_rate": "0.00%",
        "note_rate": "0.000%", "current_principal": "$0.00",
        "original_principal": "$0.00", "monthly_pi": "$0.00",
        "escrow_balance": "$0.00", "ltv": "0.00%",
        "drawn_balance": "$0.00", "heloc_limit": "$0.00",
        "base_rate": "0.000%", "margin": "0.000%",
        "annual_premium": "$0.00", "monthly_premium": "$0.00",
        "deductible": "$0.00",
        "market_value": "$0.00", "cash_value": "$0.00", "ytd_return": "0.00%",
        "locked_rate": "0.000%", "expires_at": "n/a", "loan_amount": "$0.00",
        "monthly_fee": "$0.00", "waiver_min_balance": "$0.00",
        "ref_outstanding_balance": "$0.00", "ref_ledger_balance": "$0.00",
        "ref_purchase_apr": "0.000%", "ref_apy": "0.000%",
    }

    def splice_child(prefix, child):
        if child.get("sub_type") is not None or "ledger_balance" in child:
            facts[prefix + "ledger_balance"] = money(child.get("ledger_balance"))
            facts[prefix + "available_balance"] = money(child.get("available_balance"))
            facts[prefix + "apy"] = pct(child.get("apy"))
            facts[prefix + "monthly_fee"] = money(child.get("monthly_fee"))
            facts[prefix + "waiver_min_balance"] = money(child.get("waiver_min_balance"))
        if "credit_limit" in child and child.get("purchase_apr") is not None:
            facts[prefix + "credit_limit"] = money(child.get("credit_limit"))
            facts[prefix + "outstanding_balance"] = money(child.get("outstanding_balance"))
            facts[prefix + "available_credit"] = money(child.get("available_credit"))
            facts[prefix + "purchase_apr"] = pct(child.get("purchase_apr"))
            facts[prefix + "cash_advance_apr"] = pct(child.get("cash_advance_apr"))
            facts[prefix + "rewards_rate"] = pct(child.get("rewards_rate"), 2)
        if "note_rate" in child:
            facts[prefix + "note_rate"] = pct(child.get("note_rate"))
            facts[prefix + "current_principal"] = money(child.get("current_principal"))
            facts[prefix + "original_principal"] = money(child.get("original_principal"))
            facts[prefix + "monthly_pi"] = money(child.get("monthly_pi"))
            facts[prefix + "escrow_balance"] = money(child.get("escrow_balance"))
            facts[prefix + "ltv"] = pct(child.get("ltv_at_origination"), 2)
        if "base_rate" in child:
            facts[prefix + "drawn_balance"] = money(child.get("drawn_balance"))
            facts[prefix + "heloc_limit"] = money(child.get("credit_limit"))
            facts[prefix + "base_rate"] = pct(child.get("base_rate"))
            facts[prefix + "margin"] = pct(child.get("margin"))
        if "annual_premium" in child:
            facts[prefix + "annual_premium"] = money(child.get("annual_premium"))
            facts[prefix + "monthly_premium"] = money(child.get("monthly_premium"))
            facts[prefix + "deductible"] = money(child.get("deductible"))
        if "market_value" in child:
            facts[prefix + "market_value"] = money(child.get("market_value"))
            facts[prefix + "cash_value"] = money(child.get("cash_value"))
            facts[prefix + "ytd_return"] = pct(child.get("ytd_return_pct"), 2)

    if subject is not None:
        facts["account_last4"] = str(subject["account_id"]).zfill(4)[-4:]
        splice_child("", subject["child"])
        _splice_lob_agnostic(facts, subject)
    if refs:
        splice_child("ref_", refs[0]["child"])
    return facts


def _splice_lob_agnostic(facts, subject):
    """Fill the neutral {balance_kind}/{balance}/{rate}/{payment} facts from the
    subject account's LOB, so lob-agnostic templates (rate_inquiry,
    account_balance, ...) bind to real figures regardless of which LOB the
    caller was routed to."""
    lob = subject["lob"]
    c = subject["child"]
    if lob == "banking":
        facts["balance_kind"] = "ledger balance"
        facts["balance"] = money(c["ledger_balance"])
        facts["secondary_kind"] = "available balance"
        facts["secondary_balance"] = money(c["available_balance"])
        facts["rate_label"] = "annual percentage yield (APY)"
        facts["rate"] = pct(c["apy"])
        facts["payment_label"] = "monthly maintenance fee"
        facts["payment_amount"] = money(c.get("monthly_fee"))
    elif lob == "cards":
        facts["balance_kind"] = "outstanding balance"
        facts["balance"] = money(c["outstanding_balance"])
        facts["secondary_kind"] = "available credit"
        facts["secondary_balance"] = money(c["available_credit"])
        facts["rate_label"] = "purchase APR"
        facts["rate"] = pct(c["purchase_apr"])
        facts["payment_label"] = "minimum payment"
        facts["payment_amount"] = money(max(35.0, round(c["outstanding_balance"] * 0.03, 2)))
    elif lob == "mortgage":
        facts["balance_kind"] = "current principal balance"
        facts["balance"] = money(c["current_principal"])
        facts["secondary_kind"] = "escrow balance"
        facts["secondary_balance"] = money(c["escrow_balance"])
        facts["rate_label"] = "note rate"
        facts["rate"] = pct(c["note_rate"])
        facts["payment_label"] = "scheduled principal-and-interest payment"
        facts["payment_amount"] = money(c["monthly_pi"])
    elif lob == "home":
        facts["balance_kind"] = "drawn balance"
        facts["balance"] = money(c["drawn_balance"])
        facts["secondary_kind"] = "credit limit"
        facts["secondary_balance"] = money(c["credit_limit"])
        facts["rate_label"] = "current rate (base plus margin)"
        facts["rate"] = pct(round(c["base_rate"] + c["margin"], 3))
        facts["payment_label"] = "estimated monthly payment"
        facts["payment_amount"] = money(round(c["drawn_balance"] * 0.02 + 50, 2))
    elif lob == "investments":
        facts["balance_kind"] = "market value"
        facts["balance"] = money(c["market_value"])
        facts["secondary_kind"] = "settled cash"
        facts["secondary_balance"] = money(c["cash_value"])
        facts["rate_label"] = "year-to-date return"
        facts["rate"] = pct(c["ytd_return_pct"], 2)
        facts["payment_label"] = "advisory fee (bps)"
        facts["payment_amount"] = f"{c['advisory_fee_bps']} bps"
    elif lob == "car_insurance":
        facts["balance_kind"] = "annual premium"
        facts["balance"] = money(c["annual_premium"])
        facts["secondary_kind"] = "deductible"
        facts["secondary_balance"] = money(c["deductible"])
        facts["rate_label"] = "monthly premium"
        facts["rate"] = money(c["monthly_premium"])
        facts["payment_label"] = "monthly premium"
        facts["payment_amount"] = money(c["monthly_premium"])


def build_turns(template, facts):
    turns = []
    for speaker, text in template:
        if "{" in text:
            text = text.format(**facts)
        turns.append({"speaker": speaker, "text": text})
    return turns


# Dialogue templates (CATEGORY_DIALOGUES, three variants per category, and the event
# dialogue) live in dialogue_templates.py so other tools can import them without
# running the generator.


# ── events: config, fee schedule, branches ────────────────────────────────────

CONFIG = {"branches": {}, "events": []}
FEE_CHANGE_EVENT = "fee_schedule_change"

OVERDRAFT_FEE_OLD = 29.0
OVERDRAFT_FEE_NEW = 35.0
NSF_FEE = 29.0
ATM_FEE = 3.0
MAINTENANCE_FEE_FLOOR = 5.0
FEE_NAMES = {
    "overdraft": "overdraft fee",
    "nsf": "returned item (NSF) fee",
    "monthly_maintenance": "monthly account maintenance fee",
    "atm": "out-of-network ATM fee",
}
# Share of banking fee lines per fee type, by hash of the line (no random draw).
BANKING_FEE_MIX = [("overdraft", 40), ("monthly_maintenance", 35), ("nsf", 15), ("atm", 10)]


def load_config(path):
    """Read events.yml. A missing file or an empty `events` list means a baseline-only run."""
    path = Path(path)
    cfg = yaml.safe_load(path.read_text())
    cfg["events_yml_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    cfg.setdefault("events", [])
    return cfg


def _sha_unit(text):
    """A number in [0, 1) from SHA-256 of `text`; stable across processes, unlike hash()."""
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big") / 2**64


def assign_home_branch(member_id):
    """Branch by SHA-256(member_id) against the configured shares."""
    u = _sha_unit(str(member_id))
    cum = 0.0
    names = list(CONFIG["branches"])
    for name in names:
        cum += CONFIG["branches"][name]
        if u < cum:
            return name
    return names[-1]


def fee_change_date():
    for ev in CONFIG["events"]:
        if ev["id"] == FEE_CHANGE_EVENT:
            return ev["date"]
    return None


def overdraft_price(on_date):
    change = fee_change_date()
    return OVERDRAFT_FEE_NEW if change is not None and on_date >= change else OVERDRAFT_FEE_OLD


def largest_remainder(shares, total):
    """Integer counts that sum to `total`, proportional to `shares` (ties go to earlier keys)."""
    exact = {k: Fraction(str(v)) * total for k, v in shares.items()}
    counts = {k: int(v) for k, v in exact.items()}
    left = total - sum(counts.values())
    order = sorted(exact, key=lambda k: (-(exact[k] - counts[k]), list(exact).index(k)))
    for k in order[:left]:
        counts[k] += 1
    return counts


# ── calls ─────────────────────────────────────────────────────────────────────

COMMENTS_BY_SCORE = {
    1: ["Very frustrated with the outcome.", "Problem was not resolved.",
        "Had to call back multiple times."],
    2: ["Not satisfied with the help I received.", "Took too long.",
        "Agent seemed unsure of the answer."],
    3: ["Okay experience, nothing special.", "Issue was partially resolved.",
        "Average service."],
    4: ["Good experience overall.", "Agent was helpful and professional.",
        "Minor wait time but resolved quickly."],
    5: ["Excellent service!", "Agent went above and beyond.",
        "Very satisfied — quick and easy."],
}


def is_checking(account_id):
    acct = accounts[account_id]
    return acct["lob"] == "banking" and acct["child"]["sub_type"] == "checking"


def candidate_members(category, member_accounts, branch=None, need_checking=False):
    """Members who could plausibly place a call in `category` (ascending member id)."""
    lobs = CATEGORY_LOBS[category]
    if lobs:
        pool = [m for m in range(1, N_MEMBERS + 1)
                if any(accounts[a]["lob"] in lobs for a in member_accounts[m])]
    else:
        pool = list(range(1, N_INDIVIDUALS + 1))
    if branch is not None:
        pool = [m for m in pool if members[m - 1]["home_branch"] == branch]
    if need_checking:
        pool = [m for m in pool if any(is_checking(a) for a in member_accounts[m])]
    return pool


def draw_call(rng, category, member_accounts, *, branch=None, need_checking=False, window=None):
    """Draw one call. With no restrictions the draw order is the original generator's, so the
    baseline stream (the global `random` module) is unchanged by this refactor.

    `window` is (first_day, last_day_exclusive) for event calls; baseline timing is the
    six months back from REFERENCE_DATE. Weekend dates roll back to Friday either way.
    """
    lobs = CATEGORY_LOBS[category]
    member_id = rng.choice(candidate_members(category, member_accounts, branch, need_checking))
    member = members[member_id - 1]
    caller_type = "business" if member["member_type"] == "business" else "member"

    member_acct_ids = member_accounts[member_id]
    eligible = ([a for a in member_acct_ids if accounts[a]["lob"] in lobs]
                if lobs else member_acct_ids)
    if need_checking:
        eligible = [a for a in eligible if is_checking(a)]
    if category == "rate_inquiry":
        # rate_inquiry should land on a *meaningful* rate: skip 0% checking
        # accounts so a mortgage note_rate / card APR / deposit APY shows up.
        rate_eligible = [a for a in eligible
                         if accounts[a]["lob"] != "banking"
                         or accounts[a]["child"].get("apy", 0) > 0]
        if rate_eligible:
            eligible = rate_eligible
    if not lobs:  # account_opening: brand-new account, no existing subject
        subject_id = None
        ref_ids = []
    else:
        subject_id = rng.choice(eligible) if eligible else None
        ref_ids = []
        if subject_id is not None and len(member_acct_ids) > 1 \
                and rng.random() < INTERACTION_MULTI_RATIO:
            second = [a for a in member_acct_ids if a != subject_id]
            if second:
                ref_ids = [rng.choice(second)]

    if window is None:
        day = REFERENCE_DATE - timedelta(days=rng.randint(0, 182))
    else:
        first, last = window
        day = datetime.combine(first, REFERENCE_DATE.time()) + timedelta(
            days=rng.randint(0, (last - first).days - 1))
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    started = day.replace(hour=rng.randint(8, 18), minute=rng.randint(0, 59),
                          second=rng.randint(0, 59), microsecond=0)
    duration = rng.randint(120, 900)
    outcome = rng.choices(OUTCOMES, weights=[55, 15, 12, 10, 8])[0]
    staff_id = rng.randint(1, N_STAFF)
    channel = rng.choices(CHANNELS, weights=[82, 10, 5, 3])[0]
    direction = rng.choices(DIRECTIONS, weights=[80, 12, 8])[0]
    return {
        "member_id": member_id, "caller_type": caller_type, "category": category,
        "subject_id": subject_id, "ref_ids": ref_ids, "started": started,
        "duration": duration, "outcome": outcome, "staff_id": staff_id,
        "channel": channel, "direction": direction,
    }


def draw_survey(rng, call):
    """~75% of calls get a CSAT survey; returns the row or None (same draws as before)."""
    if rng.random() < CSAT_RESPONSE_RATE:
        return generate_csat(rng, call_str(0), mbr_str(call["member_id"]),
                             call["outcome"], call["category"], call["started"])
    return None


def generate_csat(rng, call_id, member_id, outcome, category, started_at):
    if outcome == "resolved":
        score = rng.choices([4, 5], weights=[30, 70])[0]
    elif outcome == "escalated":
        score = rng.choices([2, 3, 4], weights=[20, 50, 30])[0]
    elif outcome == "unresolved":
        score = rng.choices([1, 2, 3], weights=[40, 40, 20])[0]
    else:
        score = rng.choices([3, 4, 5], weights=[30, 40, 30])[0]

    # category coupling: escrow_analysis skews negative, investment_review positive
    if category == "escrow_analysis":
        score = max(1, score - 1)
    elif category == "investment_review":
        score = min(5, score + 1)

    return {
        "call_id": call_id,
        "member_id": member_id,
        "category": category,
        "score": score,
        "comment": rng.choice(COMMENTS_BY_SCORE[score]),
        "survey_date": (started_at + timedelta(days=rng.randint(0, 3)))
        .replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
    }


def call_facts(call, extra=None):
    member = members[call["member_id"] - 1]
    subject = accounts[call["subject_id"]] if call["subject_id"] is not None else None
    refs = [accounts[r] for r in call["ref_ids"]]
    facts = make_facts(member, subject, refs)
    if extra:
        facts.update(extra)
    return facts


def baseline_calls(member_accounts, dialogue_rng):
    """The original 1,250 calls, drawn from the global stream in the original order."""
    calls = []
    for _ in range(N_INTERACTIONS):
        category = random.choices(CATEGORIES, weights=CATEGORY_W)[0]
        call = draw_call(random, category, member_accounts)
        call["csat"] = draw_survey(random, call)
        variant = dialogue_rng.randrange(N_VARIANTS)
        call["turns"] = build_turns(CATEGORY_DIALOGUES[category][variant], call_facts(call))
        call["label"] = None
        calls.append(call)
    return calls


# ── events: plan, draw, shift ─────────────────────────────────────────────────

DATA_START = (REFERENCE_DATE - timedelta(days=182)).date()
DATA_END = (REFERENCE_DATE + timedelta(days=1)).date()    # exclusive
TOTAL_WEEKS = (DATA_END - DATA_START).days / 7


def event_phases(ev):
    """Phase windows [start, end) with weeks resolved; the last may run to the end of the data."""
    out, start = [], ev["date"]
    for idx, ph in enumerate(ev["phases"]):
        end = DATA_END if ph["weeks"] == "end" else min(start + timedelta(weeks=ph["weeks"]),
                                                       DATA_END)
        out.append({"index": idx, "start": start, "end": end, "spec": ph})
        start = end
    return out


def event_branch(ev):
    return (ev.get("population") or {}).get("home_branch")


def plan_labels(ev, baseline, ev_rng):
    """Planned calls for one event: exact driver, pattern and fee-type quotas, then shuffled."""
    branch = event_branch(ev)
    phases = event_phases(ev)
    plan = []
    for ph in phases:
        weeks = (ph["end"] - ph["start"]).days / 7
        spec_labels, planned = [], {}
        for category, mult in ph["spec"]["volume"].items():
            in_scope = [c for c in baseline if c["category"] == category
                        and (branch is None or members[c["member_id"] - 1]["home_branch"] == branch)]
            rate = len(in_scope) / TOTAL_WEEKS
            n = round((mult - 1) * rate * weeks)
            drivers = [d for d in ev["drivers"] if category in d["share"]]
            counts = largest_remainder({d["id"]: d["share"][category] for d in drivers}, n)
            planned[category] = {d["id"]: counts[d["id"]] for d in drivers}
            for d in drivers:
                k = counts[d["id"]]
                fee_types = d.get("fee_type")
                if isinstance(fee_types, dict):
                    fee_alloc = largest_remainder(fee_types, k)
                    fee_list = [f for f, c in fee_alloc.items() for _ in range(c)]
                else:
                    fee_list = [fee_types] * k
                patterns = [None] * k
                variants = [None] * k
                for pat in d.get("patterns", []):
                    var_share = {v: Fraction(str(pat["share"])) / len(pat["variants"])
                                 for v in pat["variants"]}
                    alloc = largest_remainder({"": 1 - sum(var_share.values()),
                                               **{v: s for v, s in var_share.items()}}, k)
                    seq = [v for v, c in alloc.items() for _ in range(c)]
                    patterns = [pat["id"] if v else None for v in seq]
                    variants = [v or None for v in seq]
                for j in range(k):
                    spec_labels.append({
                        "event": ev["id"], "phase": ph["index"], "category": category,
                        "driver": d["id"], "pattern": patterns[j], "variant": variants[j],
                        "fee_type": fee_list[j],
                    })
        ev_rng.shuffle(spec_labels)
        plan.append({"phase": ph, "labels": spec_labels, "planned": planned})
    return plan


OVERDRAFT_DRIVERS = {"unaware_of_change", "courtesy_refund_refused", "multiple_overdrafts_same_day"}


def draw_event_calls(ev, plan, member_accounts, ev_rng, dialogue_rng):
    """Draw the calls of one event (and the ledger rows its drivers promise)."""
    branch = event_branch(ev)
    n_patterns = sum(1 for p in plan for lab in p["labels"] if lab["pattern"])
    pattern_staff = iter(ev_rng.sample(range(1, N_STAFF + 1), n_patterns))
    other_branches = [b for b in CONFIG["branches"] if b != branch]
    change_date = fee_change_date()
    calls, ledger = [], []
    for p in plan:
        ph = p["phase"]
        for label in p["labels"]:
            need_checking = label["driver"] in OVERDRAFT_DRIVERS
            call = draw_call(ev_rng, label["category"], member_accounts, branch=branch,
                             need_checking=need_checking, window=(ph["start"], ph["end"]))
            if label["pattern"]:
                call["staff_id"] = next(pattern_staff)
            call["csat"] = draw_survey(ev_rng, call)
            extra = {
                "old_fee": money(OVERDRAFT_FEE_OLD), "new_fee": money(OVERDRAFT_FEE_NEW),
                "change_date": (f"{change_date:%B} {change_date.day}" if change_date else ""),
                "branch_a": other_branches[0] if other_branches else "",
                "branch_b": other_branches[-1] if other_branches else "",
            }
            subject = accounts[call["subject_id"]] if call["subject_id"] is not None else None
            if label["driver"] == "multiple_overdrafts_same_day":
                count = ev_rng.randint(2, 4)
                call_day = call["started"].date()
                offset = ev_rng.randint(0, max(0, (call_day - ev["date"]).days - 1))
                fee_day = ev["date"] + timedelta(days=offset)
                hour = 7 if fee_day == call_day else 12
                for _ in range(count):
                    ledger.append({
                        "account_id": call["subject_id"],
                        "posted_at": datetime.combine(fee_day, time(hour)).isoformat(),
                        "amount": -overdraft_price(fee_day), "txn_type": "fee",
                        "status": "posted", "description": "Overdraft fee",
                        "fee_type": "overdraft",
                    })
                extra.update({
                    "overdraft_count": str(count),
                    "overdraft_total": money(count * overdraft_price(fee_day)),
                    "overdraft_day": f"{fee_day:%B} {fee_day.day}",
                })
            if label["driver"] == "other_fee_question":
                kind = label["fee_type"]
                if kind == "monthly_maintenance":
                    fee = subject["child"]["monthly_fee"] or MAINTENANCE_FEE_FLOOR
                else:
                    fee = {"nsf": NSF_FEE, "atm": ATM_FEE}[kind]
                extra.update({"fee_name": FEE_NAMES[kind], "fee_amount": money(fee)})
            variant = dialogue_rng.randrange(N_VARIANTS)
            template = compose_event_turns(ev["id"], label["category"], label["driver"], variant,
                                           misstatement=label["variant"])
            call["turns"] = build_turns(template, call_facts(call, extra))
            call["label"] = label
            calls.append(call)
    return calls, ledger


def shift_surveys(ev, calls, ev_rng):
    """Lower each phase's event surveys by its `csat_shift`: the integer part for every
    response, and one more point for exactly round(fraction x responses) of them."""
    for ph in event_phases(ev):
        rows = [c["csat"] for c in calls if c["label"]["phase"] == ph["index"] and c["csat"]]
        shift = Fraction(str(-ph["spec"]["csat_shift"]))
        whole, frac = int(shift), shift - int(shift)
        extra = set(ev_rng.sample(range(len(rows)), round(frac * len(rows))))
        for j, row in enumerate(rows):
            drop = whole + (1 if j in extra else 0)
            new = max(1, min(5, row["score"] - drop))
            row["shift_applied"] = row["score"] - new
            if new != row["score"]:
                row["score"] = new
                row["comment"] = ev_rng.choice(COMMENTS_BY_SCORE[new])


# ── merge, renumber, serialize ────────────────────────────────────────────────

def renumber(calls):
    """Order by (timestamp, sha256("member_id|category|timestamp")) and assign ids 1..N."""
    def key(c):
        ts = c["started"].isoformat()
        return ts, hashlib.sha256(f"{c['member_id']}|{c['category']}|{ts}".encode()).hexdigest()
    ordered = sorted(calls, key=key)
    for i, call in enumerate(ordered, start=1):
        call["interaction_id"] = i
    return ordered


def serialize_calls(calls):
    """Returns (interactions, transcripts, interaction_accounts, csat)."""
    interactions, transcripts, ia_rows, csat = [], [], [], []
    for c in calls:
        i = c["interaction_id"]
        member = members[c["member_id"] - 1]
        interactions.append({
            "interaction_id": i,
            "call_id": call_str(i),
            "member_id": c["member_id"],
            "staff_id": c["staff_id"],
            "category_code": c["category"],
            "started_at": c["started"].isoformat(),
            "ended_at": (c["started"] + timedelta(seconds=c["duration"])).isoformat(),
            "duration_seconds": c["duration"],
            "channel": c["channel"],
            "direction": c["direction"],
            "caller_type": c["caller_type"],
            "outcome": c["outcome"],
            "first_contact_resolved": c["outcome"] == "resolved",
        })
        if c["subject_id"] is not None:
            ia_rows.append({"interaction_id": i, "account_id": c["subject_id"],
                            "account_role": "subject"})
        for rid in c["ref_ids"]:
            ia_rows.append({"interaction_id": i, "account_id": rid,
                            "account_role": "referenced"})
        full_text = " ".join(f"{t['speaker'].upper()}: {t['text']}" for t in c["turns"])
        transcripts.append({
            "call_id": call_str(i),
            "date": c["started"].isoformat(),
            "duration_seconds": c["duration"],
            "category": c["category"],
            "outcome": c["outcome"],
            "member_id": mbr_str(c["member_id"]),
            "member_name": member["legal_name"],
            "agent_id": agt_str(c["staff_id"]),
            "transcript": c["turns"],
            "full_text": full_text,
        })
        if c["csat"]:
            row = {k: v for k, v in c["csat"].items() if not k.startswith("shift_")}
            row["call_id"] = call_str(i)
            csat.append(row)
    return interactions, transcripts, ia_rows, csat


def generate_interactions(member_accounts):
    """Baseline calls plus event calls. Returns (interactions, transcripts,
    interaction_accounts, csat, ledger_rows, event_calls) with ids in time order."""
    dialogue_rng = random.Random(f"dialogue:{SEED}")
    ev_rng = random.Random(f"events:{SEED}")
    baseline = baseline_calls(member_accounts, dialogue_rng)

    event_calls, ledger, event_plans = [], [], {}
    for ev in CONFIG["events"]:
        plan = plan_labels(ev, baseline, ev_rng)
        calls, rows = draw_event_calls(ev, plan, member_accounts, ev_rng, dialogue_rng)
        shift_surveys(ev, calls, ev_rng)
        event_plans[ev["id"]] = plan
        event_calls += calls
        ledger += rows

    ordered = renumber(baseline + event_calls)
    return (*serialize_calls(ordered), ledger, ordered, event_plans)


# ── 10. transactions ──────────────────────────────────────────────────────────

TX_DESC = {
    "deposit": ["Direct deposit", "Branch deposit", "Mobile check deposit"],
    "withdrawal": ["ATM withdrawal", "ACH withdrawal", "Counter withdrawal"],
    "payment": ["Loan payment", "Credit card payment", "Recurring payment"],
    "purchase": ["POS purchase", "Online purchase", "Card-not-present purchase"],
    "fee": ["Monthly maintenance fee", "Overdraft fee", "Late fee"],
    "interest": ["Interest earned", "Dividend earned", "Interest charged"],
    "premium": ["Insurance premium", "Policy premium"],
    "transfer": ["Internal transfer", "External transfer", "Zelle transfer"],
}
TXN_TYPES = {
    "banking": ["deposit", "withdrawal", "fee", "interest", "transfer"],
    "cards": ["purchase", "payment", "fee", "interest"],
    "mortgage": ["payment", "interest", "fee"],
    "home": ["payment", "interest", "fee", "withdrawal", "deposit"],
    "car_insurance": ["premium", "fee"],
    "investments": ["deposit", "withdrawal", "transfer", "fee", "interest"],
}
FEE_DESCRIPTIONS = {
    "overdraft": "Overdraft fee",
    "monthly_maintenance": "Monthly maintenance fee",
    "nsf": "Returned item (NSF) fee",
    "atm": "Out-of-network ATM fee",
}


def price_fee(account, tid, posted):
    """(fee_type, amount, description) for a fee line. The caller has already drawn the
    baseline amount and description; this replaces them with the fee schedule."""
    if account["lob"] != "banking":
        return "other", None, None          # keep the drawn amount; description fixed below
    u = _sha_unit(f"fee|{account['account_id']}|{posted.isoformat()}|{tid}") * 100
    cum = 0
    for kind, share in BANKING_FEE_MIX:
        cum += share
        if u < cum:
            break
    if kind == "overdraft":
        amount = overdraft_price(posted.date())
    elif kind == "nsf":
        amount = NSF_FEE
    elif kind == "atm":
        amount = ATM_FEE
    else:
        amount = account["child"]["monthly_fee"] or MAINTENANCE_FEE_FLOOR
    return kind, -round(amount, 2), FEE_DESCRIPTIONS[kind]


def generate_transactions():
    rows = []
    tid = 1
    for account_id in sorted(accounts.keys()):
        lob = accounts[account_id]["lob"]
        n = TXNS_PER_ACCOUNT[lob]
        for _ in range(n):
            txn_type = random.choices(TXN_TYPES[lob])[0]
            base = {
                "banking": random.uniform(20, 2500),
                "cards": random.uniform(5, 1200),
                "mortgage": random.uniform(400, 4000),
                "home": random.uniform(50, 1500),
                "car_insurance": random.uniform(50, 900),
                "investments": random.uniform(50, 10000),
            }[lob]
            amount = round(base, 2)
            if txn_type in ("deposit", "interest"):
                pass  # credit (+)
            else:
                amount = -amount  # debit (-)
            posted = REFERENCE_DATE - timedelta(days=random.randint(0, 182))
            status = random.choices(["posted", "pending", "hold"], weights=[85, 10, 5])[0]
            description = random.choice(TX_DESC[txn_type])
            row = {
                "transaction_id": tid,
                "account_id": account_id,
                "posted_at": posted.isoformat(),
                "amount": amount,
                "txn_type": txn_type,
                "status": status,
                "description": description,
                "fee_type": None,
            }
            if txn_type == "fee":
                kind, priced, desc = price_fee(accounts[account_id], tid, posted)
                row["fee_type"] = kind
                if priced is not None:
                    row["amount"], row["description"] = priced, desc
                elif description != "Late fee":
                    row["description"] = "Service fee"   # no banking-only fee name on other lines
            rows.append(row)
            tid += 1
    return rows


def renumber_transactions(rows):
    """Reassign ids in posting order so a ledger row's id says nothing about where it came from."""
    def key(r):
        digest = hashlib.sha256(
            f"{r['account_id']}|{r['txn_type']}|{r['posted_at']}|{r['amount']}".encode()).hexdigest()
        return r["posted_at"], digest, r["account_id"]
    ordered = sorted(rows, key=key)
    columns = ("account_id", "posted_at", "amount", "txn_type", "status", "description", "fee_type")
    return [{"transaction_id": i, **{c: r[c] for c in columns}}
            for i, r in enumerate(ordered, start=1)]


# ── ground truth ──────────────────────────────────────────────────────────────

def _in_window(day, window):
    return window["start"] <= day < window["end"]


def default_windows(ev, window_days=42):
    cp = ev["date"]
    length = min(window_days, (cp - DATA_START).days, (DATA_END - cp).days)
    return ({"start": cp - timedelta(days=length), "end": cp},
            {"start": cp, "end": cp + timedelta(days=length)})


def event_filters(ev):
    return ev["default_filters"]


def _matches(call, filters):
    branch = members[call["member_id"] - 1]["home_branch"]
    return (call["category"] in filters.get("category", [call["category"]])
            and branch in filters.get("home_branch", [branch]))


def _volume(scoped, window):
    return sum(1 for c in scoped if _in_window(c["started"].date(), window))


def _surveys(scoped, window):
    """Calls in scope whose survey was taken in the window (CSAT counts by `surveyed_at`)."""
    return [c for c in scoped if c["csat"]
            and _in_window(date.fromisoformat(c["csat"]["survey_date"][:10]), window)]


def _mean_score(calls):
    return round(sum(c["csat"]["score"] for c in calls) / len(calls), 4) if calls else None


def _call_ids(scoped, event_id, window, wanted):
    """Ids of the event's calls in the window whose label satisfies `wanted(label)`."""
    return sorted(call_str(c["interaction_id"]) for c in scoped
                  if c["label"] and c["label"]["event"] == event_id and wanted(c["label"])
                  and _in_window(c["started"].date(), window))


def _event_truth(ev, plan, ordered):
    """One event's entry in the answer key."""
    before, after = default_windows(ev)
    filters = event_filters(ev)
    scoped = [c for c in ordered if _matches(c, filters)]
    s_before, s_after = _surveys(scoped, before), _surveys(scoped, after)

    # The planted shift on the window's mean: nominal (what events.yml asks for) and realized
    # (what the lowered scores did after clamping to 1-5). The observed after-minus-before
    # difference also carries baseline sampling noise, so it is not recorded here.
    own = [c for c in s_after if c["label"] and c["label"]["event"] == ev["id"]]
    nominal = {p["index"]: p["spec"]["csat_shift"] for p in event_phases(ev)}
    expected = realized = None
    if s_after:
        expected = round(sum(nominal[c["label"]["phase"]] for c in own) / len(s_after), 4)
        realized = round(-sum(c["csat"]["shift_applied"] for c in own) / len(s_after), 4)

    drivers = [{"id": d["id"],
                "before": _call_ids(scoped, ev["id"], before, lambda lab, d=d: lab["driver"] == d["id"]),
                "after": _call_ids(scoped, ev["id"], after, lambda lab, d=d: lab["driver"] == d["id"])}
               for d in ev["drivers"]]
    patterns = [{"id": pat["id"], "driver": d["id"],
                 "variants": {v: _call_ids(scoped, ev["id"], after, lambda lab, v=v: lab["variant"] == v)
                              for v in pat["variants"]}}
                for d in ev["drivers"] for pat in d.get("patterns", [])]
    return {
        "id": ev["id"], "split": ev["split"], "date": ev["date"].isoformat(),
        "phases": [{"start": p["phase"]["start"].isoformat(), "end": p["phase"]["end"].isoformat(),
                    "planned": p["planned"]} for p in plan],
        "default_window": {
            "filters": filters,
            "before": {k: v.isoformat() for k, v in before.items()},
            "after": {k: v.isoformat() for k, v in after.items()},
            "metrics": {
                "call_volume": {"before": _volume(scoped, before), "after": _volume(scoped, after)},
                "csat_average_score": {"before": _mean_score(s_before), "after": _mean_score(s_after)},
                "responses": {"before": len(s_before), "after": len(s_after)},
                "csat_shift_expected": expected,
                "csat_shift_realized": realized,
            },
            "drivers": drivers,
            "patterns": patterns,
        },
    }


def build_ground_truth(cfg, ordered, plans):
    """The answer key: per event the planned counts, the default window with its realized
    metrics, and the call ids behind every driver and pattern."""
    labels = {call_str(c["interaction_id"]): c["label"] for c in ordered if c["label"]}
    calls_map = {cid: {"event": lab["event"], "phase": lab["phase"], "driver": lab["driver"],
                       "pattern": lab["pattern"], "variant": lab["variant"]}
                 for cid, lab in sorted(labels.items())}
    return {
        "version": 1, "seed": SEED, "events_yml_sha256": cfg["events_yml_sha256"],
        "data_range": {"start": DATA_START.isoformat(), "end": DATA_END.isoformat()},
        "events": [_event_truth(ev, plans[ev["id"]], ordered) for ev in cfg["events"]],
        "calls": calls_map,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    global households, members, products, accounts, CONFIG

    parser = argparse.ArgumentParser(description="Generate the seed-42 synthetic data.")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent,
                        help="where the JSON files go (default: next to this script)")
    parser.add_argument("--events", type=Path, default=Path(__file__).parent / "events.yml",
                        help="events.yml to plant (default: data/synthetic/events.yml)")
    args = parser.parse_args(argv)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Re-seed so main() gives the same output however often it is called in one process.
    random.seed(SEED)
    Faker.seed(SEED)
    faker.seed_instance(SEED)
    CONFIG = load_config(args.events)

    print("Generating households...")
    households = generate_households()

    print("Generating members...")
    members = generate_members()

    print("Generating teams + staff...")
    teams = generate_teams()
    staff = generate_staff()

    print("Generating products...")
    products = generate_products()

    print("Generating accounts + LOB children + owners...")
    core, children, owners, member_accounts, n_accounts = generate_accounts()

    # accounts index: account_id -> dict with lob + child fields (for splicing)
    accounts = {}
    for row in core:
        accounts[row["account_id"]] = {
            "account_id": row["account_id"],
            "product_id": row["product_id"],
            "primary_member_id": row["primary_member_id"],
            "lob": next(p["lob"] for p in products if p["product_id"] == row["product_id"]),
            "child": None,
        }
    for lob, child_rows in children.items():
        for child in child_rows:
            accounts[child["account_id"]]["child"] = child

    print("Generating product rates...")
    product_rates = generate_product_rates()

    print("Generating rate locks...")
    rate_locks = generate_rate_locks(member_accounts)

    print("Generating interaction categories...")
    interaction_categories = generate_interaction_categories()

    print(f"Generating {N_INTERACTIONS} baseline interactions + "
          f"{len(CONFIG['events'])} events of transcripts + csat...")
    (interactions, transcripts, ia_rows, csat,
     event_ledger, ordered, plans) = generate_interactions(member_accounts)

    print("Generating transactions...")
    transactions = renumber_transactions(generate_transactions() + event_ledger)

    ground_truth = build_ground_truth(CONFIG, ordered, plans)
    change_log = sorted(({"name": ev["change_log_name"], "date": ev["date"].isoformat()}
                         for ev in CONFIG["events"]), key=lambda r: r["date"])
    branches = [{"name": name} for name in CONFIG["branches"]]

    # ── write outputs ──
    def w(name, data):
        write_json(output_dir, name, data)

    w("transcripts.json", transcripts)
    w("csat.json", csat)

    w("households.json", households)
    w("members.json", members)
    w("teams.json", teams)
    w("staff.json", staff)
    w("products.json", products)
    w("accounts.json", core)
    w("account_owners.json", owners)
    w("mortgage_accounts.json", children["mortgage"])
    w("home_equity_accounts.json", children["home"])
    w("auto_insurance_policies.json", children["car_insurance"])
    w("banking_accounts.json", children["banking"])
    w("credit_card_accounts.json", children["cards"])
    w("investment_accounts.json", children["investments"])
    w("product_rates.json", product_rates)
    w("rate_locks.json", rate_locks)
    w("interaction_categories.json", interaction_categories)
    w("interactions.json", interactions)
    w("interaction_accounts.json", ia_rows)
    w("transactions.json", transactions)
    w("branches.json", branches)
    w("change_log.json", change_log)
    (output_dir / "ground_truth").mkdir(exist_ok=True)
    write_json(output_dir / "ground_truth", "events.json", ground_truth)

    print("\nDone.")
    print(f"  households: {len(households)}")
    print(f"  members: {len(members)} "
          f"({N_INDIVIDUALS} individual, {N_BUSINESSES} business, {N_CONTACTS} contact)")
    print(f"  teams: {len(teams)}, staff: {len(staff)}")
    print(f"  products: {len(products)}")
    print(f"  accounts: {n_accounts} (+ {len(owners)} owner rows)")
    print(f"  product_rates: {len(product_rates)}, rate_locks: {len(rate_locks)}")
    print(f"  interactions: {len(interactions)}, interaction_accounts: {len(ia_rows)}")
    print(f"  transcripts: {len(transcripts)}, csat: {len(csat)}")
    print(f"  transactions: {len(transactions)}")
    print(f"\nOutput written to {output_dir}/")


if __name__ == "__main__":
    main()
