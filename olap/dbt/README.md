# OLAP + Semantic Layer (dbt Core + MetricFlow)

Phase 2 of the semantic-layer demo: the star/snowflake models (§6 of the demo
plan) and the MetricFlow declarations that disambiguate the deliberately
ambiguous metrics (§7). This layer is **additive** — it reads the OLTP
foundation (`olap/oltp/schema.sql`, seeded by `olap/seed.py`) and writes its
own tables into the `marts` schema. It does not touch `ccai_mcp/`, `rag/`, or
the OLTP schema/seed.

## What's here

| Path | Purpose |
|---|---|
| `dbt_project.yml` | dbt project; `marts/` models materialized as tables, staging compiled ephemeral |
| `profiles.yml` | targets the docker-compose `db` Postgres (env-var overridable) |
| `models/staging/stg_account_lob.sql` | Wide per-LOB account row (CTE only) — the polymorphism that fuels the demo |
| `models/marts/dim/` | `d_date`, `d_time`, `d_member`, `d_household`, `d_staff`, `d_team`, `d_category`, `d_product`, `d_account` (snowflake→`d_product`), degenerate `d_channel`/`d_outcome`/`d_account_status` |
| `models/marts/fact/` | `f_interaction`, `f_interaction_account` (bridge), `f_csat`, `f_account_snapshot`, `f_transaction`, `f_rate_lock`, `f_rate` |
| `models/marts/semantic/semantic_models.yml` | MetricFlow semantic models (entities, the `lob` discriminator, measures) |
| `models/marts/semantic/metrics.yml` | The public metric catalog — 43 named metrics, none keeps a bare ambiguous word |
| `tests/` | Singular tests: transaction sign convention, balance sign convention, LOB-filter isolation |
| `models/marts/_*.yml` | Generic tests: referential integrity (`relationships`), keys, `accepted_values` |

## The disambiguation rule (the point of the whole thing)

There is **no** metric named `interest_rate`, `balance`, `limit`, or `lcv`.
Every ambiguous spoken word maps to N distinct, lob-filtered metrics:

| Word you say | Metrics it resolves to (each a different number) |
|---|---|
| "interest rate" | `average_mortgage_note_rate` · `weighted_mortgage_portfolio_rate` · `average_heloc_current_rate` · `average_credit_card_purchase_apr` · `average_credit_card_cash_advance_apr` · `average_deposit_apy` · `average_investment_return_pct` |
| "balance" | `banking_available_balance` (asset) · `credit_card_outstanding` (**liability**) · `mortgage_principal_balance` · `heloc_drawn_balance` · `investment_market_value` · `escrow_balance` … and `net_member_liquidity` (the only *declared* blend) |
| "LCV / LTV" | `loan_to_value` (a fact) vs `member_lifetime_value` (a declared convention) |
| "limit" | `credit_card_credit_limit` · `heloc_credit_limit` (capacity) vs `insurance_coverage_limit` (coverage) |
| "lock" | `rate_locks_count` (pipeline) — never conflated with a fraud freeze (an interaction outcome) |

The discriminator is `product.lob`, surfaced as `Dimension('product__lob')` in
every metric filter.

## Dependencies

dbt-core + dbt-postgres + MetricFlow (`dbt-metricflow`) are pinned as a uv
dependency group in `pyproject.toml`:

```bash
uv sync --group dbt        # installs the pinned dbt + MetricFlow CLI into .venv
```

Pinned versions (verified together): `dbt-core==1.12.5`,
`dbt-postgres==1.11.0` (adapter versioning is decoupled from core),
`dbt-metricflow==0.15.0` (provides the `mf` CLI).

## Prerequisites

The OLTP layer must be loaded first (see `olap/README.md`):

```bash
docker compose up -d db                    # or a reachable Postgres on :5432
olap/oltp/apply.sh                         # apply the OLTP schema
python data/synthetic/generate_data.py     # regenerate seed JSON (deterministic, seed 42)
python olap/seed.py                        # load into Postgres (idempotent)
```

## Run commands (from `olap/dbt/`)

`dbt` and `mf` both pick up `dbt_project.yml` / `profiles.yml` from the current
working directory, so run everything from this directory (or set
`DBT_PROFILES_DIR` and `DBT_PROJECT_DIR`).

```bash
cd olap/dbt

# sanity
uv run --group dbt dbt debug

# build the star tables AND run all data tests (referential integrity,
# sign-convention, lob-filter isolation)
uv run --group dbt dbt build

# re-run tests only
uv run --group dbt dbt test

# validate the MetricFlow declarations (semantic config + data warehouse)
uv run --group dbt mf validate-configs

# list / query metrics
uv run --group dbt mf list metrics
uv run --group dbt mf query --metrics banking_available_balance,credit_card_outstanding --decimals 2
```

`dbt build` first runs `dbt parse`, which emits `target/semantic_manifest.json`
— `mf` reads that artifact, so `mf validate-configs` must come **after** a
`dbt parse` / `build`.

## Worked disambiguation (real values from the seeded data, seed 42)

The single question **"what is our average interest rate?"** has no answer —
and no metric with that bare name exists. Ask the semantic layer and you get
five declared metrics side by side (via `mf query --metrics ... --explain`,
each generating its own lob-filtered SQL):

| Metric | Value |
|---|---|
| `average_mortgage_note_rate` (lob=mortgage) | 6.5888 |
| `average_credit_card_purchase_apr` (lob=cards) | 18.1192 |
| `average_deposit_apy` (lob=banking) | 1.9373 |
| `average_heloc_current_rate` (lob=home) | 10.1101 |
| `average_investment_return_pct` (lob=investments) | 7.4710 |

The sign trap, **"what is the member's balance?"**:

| Metric | Value |
|---|---|
| `banking_available_balance` (asset) | 38,287,038.69 |
| `credit_card_outstanding` (liability) | 2,354,869.83 |
| `net_member_liquidity` (derived: asset − liability) | 35,932,168.86 |

The acronym collision, **"LCV"**:

| Metric | Value |
|---|---|
| `loan_to_value` (LTV, a fact) | 77.63 |
| `member_lifetime_value` (LCV, a declared convention) | 760,635.25 |

### The LCV convention (parameterized, declared once)

`member_lifetime_value` is a **derived metric** with its formula written down:

```
member_lifetime_value = relationship_revenue × 24 / active_members
```

where:

- `relationship_revenue = total_fee_revenue + total_interest_income` (fee +
  interest income over the trailing ~6-month transaction window),
- `active_members` = distinct members with ledger activity in the window,
- `× 24` packs two declared parameters: **×2** annualizes a 6-month window,
  and **×12** is the assumed average relationship tenure in years
  (`24 = 2 × 12`).

These numbers are a **convention, not a fact** — they are parameters to argue
about, and because the formula lives in one committed YAML file, the argument
happens in a code review instead of a slide deck. §8 of the plan (Q4) leaves
this open; this is the explicitly-parameterized version the plan asked for.

### NPS mapping

`nps = (promoters - detractors) / responses × 100` on the 1–5 scale with
`5 = promoter`, `4/3 = passive`, `2/1 = detractor`.

## Scope boundary

Additive only. This layer never modifies `ccai_mcp/`, `rag/`, or the OLTP
schema/seed — the MCP metric-tool surface (a `query_metric` tool over these
declarations) is the next phase.

## Data notes

- `f_account_snapshot` is at the real `account × snapshot_date` (monthly) grain,
  but the synthetic OLTP seed carries only the *current* as-of state, so this
  build materializes a single month-end snapshot (`2026-08-31`) per account.
  Wiring in a dbt snapshot / periodic loader later changes nothing downstream.
- The seed encodes `interest` transactions only as credits (earned); the
  `interest_expense` measure is declared for when the seed emits the charged
  (debit) leg, and is `0` today.