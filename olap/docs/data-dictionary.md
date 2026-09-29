# OLTP Data Dictionary

One line per table, followed by the line-of-business (LOB) ambiguity notes.
DDL lives in `olap/oltp/schema.sql`; seed data comes from
`data/synthetic/generate_data.py` via `olap/seed.py`.

> The scout plan cited "19 tables"; the authoritative DDL (§2 of the scout
> report) enumerates **21** — all are implemented here.

| # | Table | What it holds | Seed rows |
|---|-------|---------------|-----------|
| 1 | `household` | Retail relationship groupings for pricing / household-level LCV | 150 |
| 2 | `member` | A person (`individual`) or a business entity (`business`); business *contacts* are `individual` rows linked via `business_id` (self-FK) | 700 |
| 3 | `team` | Function area a staff group belongs to (6 areas) | 8 |
| 4 | `staff` | Agents / specialists / supervisors who handle interactions | 60 |
| 5 | `product` | One row per product, tagged with its `lob` (the polymorphic discriminator) | 24 |
| 6 | `account` | A member's holding of a product — the shared polymorphic core | 1,400 |
| 7 | `account_owner` | **Junction** account↔member (primary / joint / authorized_user) — the member double-count trap | 1,533 |
| 8 | `mortgage_account` | LOB child: first-lien note rate, principal, escrow, LTV | 170 |
| 9 | `home_equity_account` | LOB child: HELOC limit, drawn balance, base + margin rate | 110 |
| 10 | `auto_insurance_policy` | LOB child: premium, coverage, deductible | 140 |
| 11 | `banking_account` | LOB child: checking/savings/MM/certificate, ledger & available balance, APY | 530 |
| 12 | `credit_card_account` | LOB child: limit, outstanding (a *liability*), purchase & cash-advance APR | 340 |
| 13 | `investment_account` | LOB child: brokerage/IRA/managed, market value, return | 110 |
| 14 | `product_rate` | Posted rates per product per effective date (teaser vs standard) | 126 |
| 15 | `rate_lock` | Mortgage/home rate-lock pipeline (active/expired/exercised/cancelled) | 500 |
| 16 | `interaction_category` | Lookup: the 14 call categories (loose `lob_id` hint, often NULL) | 14 |
| 17 | `interaction` | A contact (mostly phone) with outcome, channel, first-contact-resolved | 1,250 |
| 18 | `interaction_account` | **Junction** interaction↔account (subject vs referenced) — the "which account" trap | 1,388 |
| 19 | `interaction_transcript` | 1:1 with interaction; `utterances` JSONB mirrors `transcripts.json` | 1,250 |
| 20 | `csat_survey` | Post-call survey (1–5 + comment); `survey_id == interaction_id` | 945 |
| 21 | `account_transaction` | Signed ledger entries (credits +, debits −) — the flow that makes "balance" mean stock-vs-flow | 25,000 |

## LOB ambiguity notes (why the semantic layer exists)

There is **no** generic `balance` or `interest_rate` column. The same spoken
word lands on a different physical column per LOB, with a different sign:

| Spoken word | Where it lives per LOB (all different columns) |
|---|---|
| **balance** | `mortgage_account.current_principal` (asset) · `home_equity_account.drawn_balance` · `banking_account.ledger_balance` vs `.available_balance` · `credit_card_account.outstanding_balance` (**liability sign**) · `investment_account.market_value` vs `.cash_value` · `mortgage_account.escrow_balance` |
| **interest rate** | `mortgage_account.note_rate` (paid) · `home_equity_account.base_rate + margin` (two columns) · `credit_card_account.purchase_apr` vs `.cash_advance_apr` (two different rates on one card) · `banking_account.apy` (**earned**, sign flipped) · `investment_account.ytd_return_pct` (not interest at all) · `product_rate.rate_value` (posted teaser vs standard) |
| **limit** | `home_equity_account.credit_limit` · `credit_card_account.credit_limit` · `auto_insurance_policy.deductible` (a coverage "limit", not credit) |
| **premium** | `auto_insurance_policy.annual_premium` (money) vs `home_equity_account.margin` (a *rate* premium) vs `f_transaction` premium flows |
| **LCV / LTV** | `mortgage_account.ltv_at_origination` (loan-to-value, a fact) vs "lifetime customer value" (a marketing convention that exists nowhere as a column) — homophone/acronym near-miss, plus "LOC" (line of credit) in transcript text |
| **lock** | `rate_lock.status` (mortgage pipeline) vs a fraud account freeze (an interaction outcome, no table) |

These collisions are deliberate: they are what the dbt + MetricFlow layer will
have to resolve by declaring separate measures filtered on `product.lob`,
instead of a BI tool encouraging one blended number.