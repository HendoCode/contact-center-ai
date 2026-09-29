-- ============================================================================
-- Meridian Valley Credit Union — OLTP foundation schema
--
-- The intentionally *denormalized-by-line-of-business* OLTP layer for the
-- semantic-layer demo. There is deliberately NO single generic "balance" or
-- "interest rate" column: every line of business stores the concept under its
-- own name and sign convention. That polymorphic ambiguity is the entire
-- point of the demo (see olap/docs/erd.md and olap/docs/data-dictionary.md).
--
-- Conventions:
--   * PKs/FKs and CHECK constraints are all explicitly named so error
--     messages and future ALTERs are legible.
--   * This file is applied by olap/oltp/apply.sh against the docker-compose
--     `db` service (pgvector/pgvector:pg16), or any Postgres reachable via
--     DATABASE_URL. It is idempotent via CREATE TABLE IF NOT EXISTS + ALTER
--     ... ADD CONSTRAINT IF NOT EXISTS (Postgres 16+).
--   * Table order follows the dependency graph: parents before children.
-- ============================================================================

BEGIN;

-- ── household ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS household (
    household_id   UUID PRIMARY KEY,
    household_label TEXT NOT NULL,
    -- '<$50k', '$50-100k', '$100-200k', '>$200k' (bucket, synthetic-safe)
    income_band    TEXT NOT NULL,
    CONSTRAINT chk_household_income_band CHECK (
        income_band IN ('<$50k', '$50-100k', '$100-200k', '>$200k')
    )
);

-- ── member ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS member (
    member_id          BIGINT PRIMARY KEY,
    household_id       UUID NULL,
    member_type        TEXT NOT NULL,
    legal_name         TEXT NOT NULL,
    email              TEXT,
    phone              TEXT,
    joined_date        DATE,
    credit_score_band  TEXT,
    residence_state    TEXT,
    -- contact-of link for business contacts (self-referential FK)
    business_id        BIGINT NULL,
    naics_code         TEXT NULL,
    employee_count     INT NULL,
    CONSTRAINT fk_member_household
        FOREIGN KEY (household_id) REFERENCES household (household_id),
    CONSTRAINT fk_member_business
        FOREIGN KEY (business_id) REFERENCES member (member_id),
    CONSTRAINT chk_member_type CHECK (member_type IN ('individual', 'business')),
    CONSTRAINT chk_member_credit_band CHECK (
        credit_score_band IS NULL OR
        credit_score_band IN ('excellent', 'good', 'fair', 'poor')
    )
);

-- ── team ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS team (
    team_id       INT PRIMARY KEY,
    team_name     TEXT NOT NULL,
    function_area TEXT NOT NULL,
    CONSTRAINT chk_team_function_area CHECK (
        function_area IN ('loans', 'insurance', 'cards', 'fraud', 'servicing', 'investments')
    )
);

-- ── staff ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS staff (
    staff_id  INT PRIMARY KEY,
    full_name TEXT NOT NULL,
    team_id   INT NOT NULL,
    role      TEXT NOT NULL,
    hire_date DATE,
    CONSTRAINT fk_staff_team
        FOREIGN KEY (team_id) REFERENCES team (team_id),
    CONSTRAINT chk_staff_role CHECK (role IN ('agent', 'specialist', 'supervisor'))
);

-- ── product ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS product (
    product_id   INT PRIMARY KEY,
    lob          TEXT NOT NULL,
    product_code TEXT NOT NULL,
    product_name TEXT NOT NULL,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT chk_product_lob CHECK (
        lob IN ('mortgage', 'home', 'car_insurance', 'banking', 'cards', 'investments')
    )
);

-- ── account (shared core — the polymorphic parent) ───────────────────────────

CREATE TABLE IF NOT EXISTS account (
    account_id         BIGINT PRIMARY KEY,
    product_id         INT NOT NULL,
    primary_member_id  BIGINT NOT NULL,
    account_number     TEXT NOT NULL,
    opened_date        DATE,
    status             TEXT NOT NULL DEFAULT 'open',
    opened_by_staff_id INT NULL,
    CONSTRAINT fk_account_product
        FOREIGN KEY (product_id) REFERENCES product (product_id),
    CONSTRAINT fk_account_primary_member
        FOREIGN KEY (primary_member_id) REFERENCES member (member_id),
    CONSTRAINT fk_account_opened_by_staff
        FOREIGN KEY (opened_by_staff_id) REFERENCES staff (staff_id),
    CONSTRAINT chk_account_status CHECK (
        status IN ('open', 'closed', 'delinquent', 'charge_off')
    )
);

-- ── account_owner (junction: account ↔ member, joint/authorized) ─────────────

CREATE TABLE IF NOT EXISTS account_owner (
    account_id     BIGINT NOT NULL,
    member_id      BIGINT NOT NULL,
    ownership_role TEXT NOT NULL,
    CONSTRAINT pk_account_owner PRIMARY KEY (account_id, member_id),
    CONSTRAINT fk_account_owner_account
        FOREIGN KEY (account_id) REFERENCES account (account_id),
    CONSTRAINT fk_account_owner_member
        FOREIGN KEY (member_id) REFERENCES member (member_id),
    CONSTRAINT chk_account_owner_role CHECK (
        ownership_role IN ('primary', 'joint', 'authorized_user')
    )
);

-- ── Per-LOB extension tables (Table-Per-Type) ────────────────────────────────
-- Each row is 1:0..1 with account; each LOB columns ITS OWN notion of
-- "balance"/"rate"/"limit". This is the built-in ambiguity the semantic layer
-- must disambiguate. The runtime discriminator is product.lob.

CREATE TABLE IF NOT EXISTS mortgage_account (
    account_id          BIGINT PRIMARY KEY,
    original_principal  NUMERIC(14, 2) NOT NULL,
    current_principal   NUMERIC(14, 2) NOT NULL,   -- "balance" meaning 1
    note_rate           NUMERIC(6, 3) NOT NULL,    -- "interest rate" 1 (paid)
    term_months         INT NOT NULL,
    monthly_pi          NUMERIC(10, 2),
    escrow_balance      NUMERIC(12, 2),            -- "balance" meaning 2
    ltv_at_origination  NUMERIC(5, 2),             -- LTV (collides with LCV)
    property_state      TEXT,
    CONSTRAINT fk_mortgage_account_account
        FOREIGN KEY (account_id) REFERENCES account (account_id)
);

CREATE TABLE IF NOT EXISTS home_equity_account (
    account_id         BIGINT PRIMARY KEY,
    credit_limit       NUMERIC(14, 2) NOT NULL,    -- "limit" meaning 1
    drawn_balance      NUMERIC(14, 2) NOT NULL,    -- "balance" meaning 3
    base_rate          NUMERIC(6, 3) NOT NULL,     -- "interest rate" 2 (base)
    margin             NUMERIC(6, 3) NOT NULL,     -- "interest rate" 2 (margin)
    draw_period_months INT NOT NULL,
    CONSTRAINT fk_home_equity_account_account
        FOREIGN KEY (account_id) REFERENCES account (account_id)
);

CREATE TABLE IF NOT EXISTS auto_insurance_policy (
    account_id      BIGINT PRIMARY KEY,
    annual_premium  NUMERIC(10, 2) NOT NULL,       -- "premium" meaning 1
    monthly_premium NUMERIC(8, 2),
    coverage_code   TEXT NOT NULL,
    deductible      NUMERIC(8, 2),                 -- "limit" meaning 2
    vehicle_year    INT,
    CONSTRAINT fk_auto_insurance_account
        FOREIGN KEY (account_id) REFERENCES account (account_id),
    CONSTRAINT chk_auto_insurance_coverage CHECK (
        coverage_code IN ('liability', 'full', 'liab+coll')
    )
);

CREATE TABLE IF NOT EXISTS banking_account (
    account_id         BIGINT PRIMARY KEY,
    sub_type           TEXT NOT NULL,
    ledger_balance     NUMERIC(14, 2) NOT NULL,    -- "balance" meaning 4a
    available_balance  NUMERIC(14, 2) NOT NULL,    -- "balance" meaning 4b
    apy                NUMERIC(6, 3),              -- "interest rate" 3 (earned)
    monthly_fee        NUMERIC(6, 2),
    waiver_min_balance NUMERIC(12, 2),
    CONSTRAINT fk_banking_account_account
        FOREIGN KEY (account_id) REFERENCES account (account_id),
    CONSTRAINT chk_banking_account_sub_type CHECK (
        sub_type IN ('checking', 'savings', 'money_market', 'certificate')
    )
);

CREATE TABLE IF NOT EXISTS credit_card_account (
    account_id          BIGINT PRIMARY KEY,
    credit_limit        NUMERIC(14, 2) NOT NULL,   -- "limit" meaning 3
    outstanding_balance NUMERIC(14, 2) NOT NULL,   -- "balance" 5 (LIABILITY sign)
    available_credit    NUMERIC(14, 2),
    purchase_apr        NUMERIC(6, 3) NOT NULL,    -- "interest rate" 4
    cash_advance_apr    NUMERIC(6, 3),              -- "interest rate" 5
    rewards_rate        NUMERIC(4, 2),
    CONSTRAINT fk_credit_card_account_account
        FOREIGN KEY (account_id) REFERENCES account (account_id)
);

CREATE TABLE IF NOT EXISTS investment_account (
    account_id      BIGINT PRIMARY KEY,
    inv_type        TEXT NOT NULL,
    market_value    NUMERIC(14, 2) NOT NULL,       -- "balance" meaning 6
    cost_basis      NUMERIC(14, 2),
    cash_value      NUMERIC(14, 2),                -- "balance" meaning 7
    ytd_return_pct  NUMERIC(7, 4),                 -- "return" (NOT interest)
    advisory_fee_bps INT,
    CONSTRAINT fk_investment_account_account
        FOREIGN KEY (account_id) REFERENCES account (account_id),
    CONSTRAINT chk_investment_account_type CHECK (
        inv_type IN ('brokerage', 'ira', 'managed')
    )
);

-- ── product_rate (posted rates — the third meaning of "rate") ────────────────

CREATE TABLE IF NOT EXISTS product_rate (
    product_rate_id BIGINT PRIMARY KEY,
    product_id      INT NOT NULL,
    effective_date  DATE NOT NULL,
    rate_type       TEXT NOT NULL,
    rate_value      NUMERIC(7, 4) NOT NULL,
    is_teaser       BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT fk_product_rate_product
        FOREIGN KEY (product_id) REFERENCES product (product_id),
    CONSTRAINT chk_product_rate_type CHECK (
        rate_type IN ('note', 'purchase_apr', 'cash_advance_apr', 'base', 'apy')
    )
);

-- ── rate_lock (mortgage/home pipeline) ───────────────────────────────────────

CREATE TABLE IF NOT EXISTS rate_lock (
    lock_id          BIGINT PRIMARY KEY,
    member_id        BIGINT NOT NULL,
    product_id       INT NOT NULL,
    locked_rate      NUMERIC(6, 3) NOT NULL,
    loan_amount      NUMERIC(14, 2) NOT NULL,
    locked_at        TIMESTAMPTZ NOT NULL,
    expires_at       TIMESTAMPTZ NOT NULL,
    lock_period_days INT NOT NULL,
    status           TEXT NOT NULL,
    CONSTRAINT fk_rate_lock_member
        FOREIGN KEY (member_id) REFERENCES member (member_id),
    CONSTRAINT fk_rate_lock_product
        FOREIGN KEY (product_id) REFERENCES product (product_id),
    CONSTRAINT chk_rate_lock_status CHECK (
        status IN ('active', 'expired', 'exercised', 'cancelled')
    )
);

-- ── interaction_category ─────────────────────────────────────────────────────
-- NB: category != lob. A card call can be a fraud call, so lob_id here is a
-- loose informational link, not a hard mapping, and is frequently NULL.

CREATE TABLE IF NOT EXISTS interaction_category (
    category_code TEXT PRIMARY KEY,
    category_name TEXT NOT NULL,
    lob_id        INT NULL
);

-- ── interaction ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS interaction (
    interaction_id         BIGINT PRIMARY KEY,
    member_id              BIGINT NOT NULL,
    staff_id               INT NOT NULL,
    category_code          TEXT NOT NULL,
    started_at             TIMESTAMPTZ,
    ended_at               TIMESTAMPTZ,
    duration_seconds       INT,
    channel                TEXT NOT NULL,
    direction              TEXT NOT NULL,
    caller_type            TEXT NOT NULL,
    outcome                TEXT NOT NULL,
    first_contact_resolved BOOLEAN,
    CONSTRAINT fk_interaction_member
        FOREIGN KEY (member_id) REFERENCES member (member_id),
    CONSTRAINT fk_interaction_staff
        FOREIGN KEY (staff_id) REFERENCES staff (staff_id),
    CONSTRAINT fk_interaction_category
        FOREIGN KEY (category_code) REFERENCES interaction_category (category_code),
    CONSTRAINT chk_interaction_channel CHECK (
        channel IN ('phone', 'chat', 'secure_message', 'branch')
    ),
    CONSTRAINT chk_interaction_direction CHECK (
        direction IN ('inbound', 'outbound', 'callback')
    ),
    CONSTRAINT chk_interaction_caller_type CHECK (
        caller_type IN ('member', 'business', 'internal')
    ),
    CONSTRAINT chk_interaction_outcome CHECK (
        outcome IN ('resolved', 'escalated', 'callback_scheduled', 'unresolved', 'transferred')
    )
);

-- ── interaction_account (junction: which accounts a call was about) ──────────

CREATE TABLE IF NOT EXISTS interaction_account (
    interaction_id BIGINT NOT NULL,
    account_id     BIGINT NOT NULL,
    account_role   TEXT NOT NULL,
    CONSTRAINT pk_interaction_account PRIMARY KEY (interaction_id, account_id),
    CONSTRAINT fk_interaction_account_interaction
        FOREIGN KEY (interaction_id) REFERENCES interaction (interaction_id),
    CONSTRAINT fk_interaction_account_account
        FOREIGN KEY (account_id) REFERENCES account (account_id),
    CONSTRAINT chk_interaction_account_role CHECK (
        account_role IN ('subject', 'referenced')
    )
);

-- ── interaction_transcript (1:1; mirrors transcripts.json exactly) ───────────

CREATE TABLE IF NOT EXISTS interaction_transcript (
    interaction_id BIGINT PRIMARY KEY,
    full_text      TEXT,
    utterances     JSONB,           -- [{speaker, text}, ...] same shape as today
    turn_count     INT,
    CONSTRAINT fk_interaction_transcript_interaction
        FOREIGN KEY (interaction_id) REFERENCES interaction (interaction_id)
);

-- ── csat_survey ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS csat_survey (
    survey_id     BIGINT PRIMARY KEY,
    interaction_id BIGINT NOT NULL UNIQUE,
    score         INT NOT NULL,
    comment       TEXT,
    surveyed_at   DATE,
    question_code TEXT NOT NULL DEFAULT 'overall',
    CONSTRAINT fk_csat_survey_interaction
        FOREIGN KEY (interaction_id) REFERENCES interaction (interaction_id),
    CONSTRAINT chk_csat_survey_score CHECK (score BETWEEN 1 AND 5)
);

-- ── account_transaction (ledger entries — makes "balance" a flow vs stock) ───

CREATE TABLE IF NOT EXISTS account_transaction (
    transaction_id BIGINT PRIMARY KEY,
    account_id     BIGINT NOT NULL,
    posted_at      TIMESTAMPTZ,
    amount         NUMERIC(14, 2) NOT NULL,   -- signed (credits +, debits -)
    txn_type       TEXT NOT NULL,
    status         TEXT NOT NULL,
    description    TEXT,
    CONSTRAINT fk_account_transaction_account
        FOREIGN KEY (account_id) REFERENCES account (account_id),
    CONSTRAINT chk_account_transaction_type CHECK (
        txn_type IN ('deposit', 'withdrawal', 'payment', 'purchase', 'fee', 'interest', 'premium', 'transfer')
    ),
    CONSTRAINT chk_account_transaction_status CHECK (
        status IN ('posted', 'pending', 'hold')
    )
);

COMMIT;