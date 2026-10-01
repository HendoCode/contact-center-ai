"""
Synthetic data generator for contact-center-ai portfolio project.

Generates realistic (but entirely fake) call-center and OLTP data for
"Meridian Valley Credit Union" (MVCU), the fictional CU:

  * The two legacy RAG/MCP outputs, BYTE-COMPATIBLE with the originals:
      - data/synthetic/transcripts.json   (now 1,250 calls, same key shape)
      - data/synthetic/csat.json          (now ~940 surveys, same key shape)
  * The OLTP foundation JSON files (one per table) loaded by olap/seed.py:
      households, members, teams, staff, products, accounts (+6 LOB child
      table files), account_owners, product_rates, rate_locks,
      interaction_categories, interactions, interaction_accounts, transactions.

Determinism: a single seeded RNG (random.seed(42)) plus Faker.seed(42), used in
a strictly fixed order, so TWO RUNS ARE BIT-IDENTICAL. Members are persistent
identities (the old generator re-rolled them every call).

The transcripts are *numerically bound to accounts*: dialogue templates splice
the caller's real note_rate / ledger_balance / purchase_apr / apy / etc. so the
RAG view (transcripts.json) and the OLAP view (Postgres) agree on the same
number from different engines.

Usage:
    python data/synthetic/generate_data.py
"""

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from faker import Faker

# ── Seeds / determinism anchors ───────────────────────────────────────────────

random.seed(42)
faker = Faker()
Faker.seed(42)
faker.seed_instance(42)

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


def build_turns(category, facts):
    template = CATEGORY_DIALOGUES[category]
    turns = []
    for speaker, text in template:
        if "{" in text:
            text = text.format(**facts)
        turns.append({"speaker": speaker, "text": text})
    return turns


# Dialogue templates. Placeholders are spliced with real account figures.
CATEGORY_DIALOGUES = {
    "account_balance": [
        ("member", "Hi, I'd like to check my account balance."),
        ("agent", "Of course. Can you verify your member number for me?"),
        ("member", "Sure, it's {member_id}."),
        ("agent", "Thanks, {member_name}. Your account ending in {account_last4} shows a {balance_kind} of {balance}."),
        ("member", "Is that the same number I'd see in online banking?"),
        ("agent", "It should be — your {secondary_kind} today is {secondary_balance}."),
    ],
    "online_banking_support": [
        ("member", "I can't sign in to online banking. It keeps saying my password is wrong."),
        ("agent", "I can help reset that. Can you verify your member number and the last four of the account you're signing into?"),
        ("member", "Sure. {member_id}, and the account ends in {account_last4}."),
        ("agent", "Thank you, {member_name}. I've sent a secure password reset link to the email on file."),
        ("member", "Got it. Will that take effect immediately?"),
        ("agent", "Yes, within a minute or two. You'll then see your balance of {ledger_balance} on the dashboard."),
    ],
    "card_services": [
        ("member", "I have a question about my credit card."),
        ("agent", "I'd be happy to help. I've verified {member_id}. Which card are we looking at?"),
        ("member", "The one ending in {account_last4}."),
        ("agent", "Got it — that's your Visa with a {credit_limit} limit, {outstanding_balance} outstanding, and a purchase APR of {purchase_apr}."),
        ("member", "And what's my available credit?"),
        ("agent", "You have {available_credit} available right now."),
    ],
    "fraud_dispute": [
        ("member", "I see a charge I don't recognize on my account. It's from an online retailer."),
        ("agent", "I understand — let me look at that. On the account ending in {account_last4}, your {balance_kind} is {balance}."),
        ("member", "I definitely didn't make that purchase."),
        ("agent", "I've opened a dispute and issued a provisional credit, so your balance won't be affected while we investigate."),
        ("member", "How long until the full resolution?"),
        ("agent", "Typically 10 business days."),
    ],
    "payment_assistance": [
        ("member", "I'm having trouble making a payment this month."),
        ("agent", "I'm sorry to hear that. For the account ending in {account_last4}, your {payment_label} is {payment_amount}."),
        ("member", "Right now that's more than I can manage."),
        ("agent", "We have a hardship program that may defer this month's payment to the end of the term. Would you like me to submit that request?"),
        ("member", "Yes, please."),
        ("agent", "Done. You'll get a confirmation by email shortly."),
    ],
    "rate_inquiry": [
        ("member", "I'd like to know what interest rate I'm currently paying."),
        ("agent", "Happy to help. Let me pull up the account ending in {account_last4}."),
        ("member", "What does it show?"),
        ("agent", "For that account, your {rate_label} is {rate}."),
        ("member", "Is that fixed, or can it change?"),
        ("agent", "That depends on the product. For reference, your current {balance_kind} is {balance}."),
    ],
    "loan_inquiry": [
        ("member", "I'd like to ask about your current loan rates."),
        ("agent", "Of course. Our posted rates vary by product — can I ask what you're looking to finance?"),
        ("member", "Something comparable to my account ending in {account_last4}."),
        ("agent", "Understood. For a comparable product today, the {rate_label} would be {rate}, and your existing {balance_kind} is {balance}."),
        ("member", "Thanks, that's helpful."),
        ("agent", "Would you like me to start a pre-qualification?"),
    ],
    "mortgage_inquiry": [
        ("member", "I'd like to check on my mortgage."),
        ("agent", "Sure — the mortgage ending in {account_last4} has a note rate of {note_rate} and a current principal of {current_principal}."),
        ("member", "What's my loan-to-value now? I saw LTV mentioned on my statement."),
        ("agent", "Your LTV at origination was {ltv}. Your escrow balance is {escrow_balance}."),
        ("member", "Great, that matches what I expected."),
        ("agent", "Is there anything else about the mortgage I can look into?"),
    ],
    "insurance_service": [
        ("member", "I'd like to review my auto insurance policy."),
        ("agent", "Of course. Your policy shows an annual premium of {annual_premium}, which is {monthly_premium} a month, with a {deductible} deductible."),
        ("member", "Can you walk through the coverage?"),
        ("agent", "Happy to. Your current coverage includes the liability and collision components you selected."),
        ("member", "And what would full coverage add?"),
        ("agent", "Great question — I can quote that for your vehicle right now if you'd like."),
    ],
    "investment_review": [
        ("member", "I'd like to review my investment account."),
        ("agent", "Let's take a look. Your market value is {market_value}, with {cash_value} in settled cash."),
        ("member", "How has it performed this year?"),
        ("agent", "Your year-to-date return is {ytd_return}. Your cost basis sits at roughly {market_value} versus what you contributed."),
        ("member", "That's good to know. I appreciate the review."),
        ("agent", "My pleasure — as a long-time member, your lifetime value to the credit union is important to us, and we're glad to keep the advice coming."),
    ],
    "account_opening": [
        ("member", "I'd like to open a new account."),
        ("agent", "Wonderful! I've verified {member_id}. Were you thinking checking or savings?"),
        ("member", "A savings account, please."),
        ("agent", "Great choice. Our high-yield savings earns {new_account_apy} right now."),
        ("member", "That's a good rate. Let's open it."),
        ("agent", "I'll get that started — you'll receive the account details in a few minutes."),
    ],
    "fee_dispute": [
        ("member", "I see a fee on my account I wasn't expecting."),
        ("agent", "Let me look. On the account ending in {account_last4}, I see a monthly fee of {monthly_fee}."),
        ("member", "I thought that was waived."),
        ("agent", "It is waived when you keep a minimum balance of {waiver_min_balance}. I can refund this month's fee as a courtesy."),
        ("member", "That would be great, thank you."),
        ("agent", "Done. I've refunded it and noted your account."),
    ],
    "escrow_analysis": [
        ("member", "I got a notice about my escrow account."),
        ("agent", "Let me pull up your escrow analysis. Your escrow balance is {escrow_balance}, and your escrow payment is included in the {monthly_pi} monthly payment."),
        ("member", "Is there a shortage?"),
        ("agent", "It looks like your property taxes changed slightly, so there's a small adjustment. Not much, but worth reviewing."),
    ],
    "rate_lock_status": [
        ("member", "I want to check on my rate lock."),
        ("agent", "No problem. I can see an active lock on your application with a rate of {locked_rate}."),
        ("member", "When does it expire?"),
        ("agent", "It's locked through {expires_at}, so you're covered while we finish underwriting."),
        ("member", "What was the loan amount again?"),
        ("agent", "The application is for {loan_amount}."),
    ],
}


def generate_interactions(member_accounts):
    """Returns (interactions, transcripts, interaction_accounts, csat)."""
    interactions = []
    transcripts = []
    ia_rows = []
    csat = []

    members_by_type = {
        "individual": list(range(1, N_INDIVIDUALS + 1)),
        "business": list(range(N_INDIVIDUALS + 1, N_INDIVIDUALS + N_BUSINESSES + 1)),
    }

    for i in range(1, N_INTERACTIONS + 1):
        category = random.choices(CATEGORIES, weights=CATEGORY_W)[0]

        # choose a member who actually holds an account in this category's LOB
        lobs = CATEGORY_LOBS[category]
        if lobs:
            candidate = [m for m in range(1, N_MEMBERS + 1)
                         if any(accounts[a]["lob"] in lobs
                                for a in member_accounts[m])]
        else:
            candidate = list(range(1, N_INDIVIDUALS + 1))
        member_id = random.choice(candidate)
        member = members[member_id - 1]
        caller_type = ("business" if member["member_type"] == "business"
                       else "member")

        # subject account (and, for 20% of calls, a referenced second account)
        member_acct_ids = member_accounts[member_id]
        eligible = [a for a in member_acct_ids
                    if accounts[a]["lob"] in lobs] if lobs else member_acct_ids
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
            subject_id = random.choice(eligible) if eligible else None
            ref_ids = []
            if subject_id is not None and len(member_acct_ids) > 1 \
                    and random.random() < INTERACTION_MULTI_RATIO:
                second = [a for a in member_acct_ids if a != subject_id]
                if second:
                    ref_ids = [random.choice(second)]

        # timing: 6 months back, weekday-weighted, business hours
        day = REFERENCE_DATE - timedelta(days=random.randint(0, 182))
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        started = day.replace(hour=random.randint(8, 18), minute=random.randint(0, 59),
                              second=random.randint(0, 59), microsecond=0)
        duration = random.randint(120, 900)
        outcome = random.choices(OUTCOMES, weights=[55, 15, 12, 10, 8])[0]
        fcr = outcome == "resolved"
        staff_id = random.randint(1, N_STAFF)

        interactions.append({
            "interaction_id": i,
            "call_id": call_str(i),
            "member_id": member_id,
            "staff_id": staff_id,
            "category_code": category,
            "started_at": started.isoformat(),
            "ended_at": (started + timedelta(seconds=duration)).isoformat(),
            "duration_seconds": duration,
            "channel": random.choices(CHANNELS, weights=[82, 10, 5, 3])[0],
            "direction": random.choices(DIRECTIONS, weights=[80, 12, 8])[0],
            "caller_type": caller_type,
            "outcome": outcome,
            "first_contact_resolved": fcr,
        })

        # interaction_account junction
        if subject_id is not None:
            ia_rows.append({"interaction_id": i, "account_id": subject_id,
                            "account_role": "subject"})
        for rid in ref_ids:
            ia_rows.append({"interaction_id": i, "account_id": rid,
                            "account_role": "referenced"})

        # transcript (numerically bound to the account rows)
        subject = accounts[subject_id] if subject_id is not None else None
        refs = [accounts[r] for r in ref_ids]
        facts = make_facts(member, subject, refs)
        # everything the template placeholders need is present in `facts`
        turns = build_turns(category, facts)
        full_text = " ".join(f"{t['speaker'].upper()}: {t['text']}" for t in turns)
        transcripts.append({
            "call_id": call_str(i),
            "date": started.isoformat(),
            "duration_seconds": duration,
            "category": category,
            "outcome": outcome,
            "member_id": mbr_str(member_id),
            "member_name": member["legal_name"],
            "agent_id": agt_str(staff_id),
            "transcript": turns,
            "full_text": full_text,
        })

        # csat (~75%)
        if random.random() < CSAT_RESPONSE_RATE:
            csat.append(generate_csat(
                call_str(i), mbr_str(member_id), outcome, category, started))

    return interactions, transcripts, ia_rows, csat


def generate_csat(call_id, member_id, outcome, category, started_at):
    if outcome == "resolved":
        score = random.choices([4, 5], weights=[30, 70])[0]
    elif outcome == "escalated":
        score = random.choices([2, 3, 4], weights=[20, 50, 30])[0]
    elif outcome == "unresolved":
        score = random.choices([1, 2, 3], weights=[40, 40, 20])[0]
    else:
        score = random.choices([3, 4, 5], weights=[30, 40, 30])[0]

    # category coupling: escrow_analysis skews negative, investment_review positive
    if category == "escrow_analysis":
        score = max(1, score - 1)
    elif category == "investment_review":
        score = min(5, score + 1)

    comments_by_score = {
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

    return {
        "call_id": call_id,
        "member_id": member_id,
        "category": category,
        "score": score,
        "comment": random.choice(comments_by_score[score]),
        "survey_date": (started_at + timedelta(days=random.randint(0, 3))) \
            .replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
    }


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
            rows.append({
                "transaction_id": tid,
                "account_id": account_id,
                "posted_at": posted.isoformat(),
                "amount": amount,
                "txn_type": txn_type,
                "status": random.choices(["posted", "pending", "hold"],
                                         weights=[85, 10, 5])[0],
                "description": random.choice(TX_DESC[txn_type]),
            })
            tid += 1
    return rows


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    global households, members, products, accounts

    output_dir = Path(__file__).parent

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

    print(f"Generating {N_INTERACTIONS} interactions + transcripts + csat...")
    interactions, transcripts, ia_rows, csat = generate_interactions(member_accounts)

    print("Generating transactions...")
    transactions = generate_transactions()

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
