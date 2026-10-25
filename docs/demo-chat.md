# Demo chat: a supervisor, six tools, five ambiguous words

A sample conversation between a call-center supervisor and the MCP server, to show what each
tool returns and how the semantic layer settles words like "rate", "balance" and "LTV". It
follows one member, **Mary Miller** (`MBR-000124`, seed-42 synthetic
data), through that member's calls (turns 1 to 7), then widens to the whole call center: why a
metric moved after a change, and which fee "fees" means (turns 8 and 9).

Generated 2026-10-25 from commit `facad57 (+ uncommitted changes)` by `tools/demo_chat.py`, against Postgres
16.15 (Debian 16.15-1.pgdg12+2), 1,540 calls and 1147 CSAT responses, marts built by `dbt build`,
Python 3.12.14, dbt-core 1.12.5, dbt-postgres 1.11.0, dbt-metricflow 0.15.0, langgraph 1.2.12.

## How to read it

Each turn is **User**, **Tool call** (name and JSON arguments), **Tool result**, **Answer**.
Tool results are verbatim except that the generated SQL is moved into a fold under the answer.
Three kinds of content, never mixed within a block:

| Marker | What it is |
|---|---|
| (no marker) | Captured. The tool or graph ran against the seeded stack and this is what it returned. |
| **Scripted step** | A decision a chat model would make, replaced by a fixed value because there is no model offline. Everything downstream of it ran for real. |
| **ILLUSTRATIVE** | Not run. Shown for shape, with the command that captures it. |

The user's messages and the choice of which tool to call are scripted: no chat model was
available when this was generated. Tool calls are made through the MCP server over stdio, as a
client such as Claude Desktop would. A client that already knows the catalog's metric names
calls `query_metric` directly (turns 6 and 7); the agent asks first (turn 5). Where an **Answer** is not a verbatim tool or graph output,
the sentences are filled in by the generator from the captured values, so a refresh changes them
with the data.

Regenerate (with the stack from `docs/TOUR.md` section B2 running):

```bash
make ingest                                    # transcripts into pgvector
uv run --group agent --group dbt python -m tools.demo_chat
```

---

## Turn 1: what are members saying about loan rates?

**User:** What are members saying when they call about current loan rates?

**Tool call:** `search_transcripts`

```json
{
  "query": "what are members saying when they call about current loan rates?",
  "k": 5
}
```

**Tool result / Answer:**

> **ILLUSTRATIVE, not captured output.** Written by hand to show the shape of the answer from what the transcript says; the wording and the retrieved set depend on the chat and embedding models. To capture it, run:
> `uv run python -c "from ccai_mcp.tools import search_transcripts; print(search_transcripts('what are members saying when they call about current loan rates?', k=5))"`
>
> In CALL-00421 the member asks about current loan rates for something comparable to an account ending 0531, is quoted a note rate and the existing principal balance, and is offered a pre-qualification; the call is logged unresolved.

`search_transcripts` embeds the query, takes the five nearest transcripts from pgvector and has
the chat model answer from them. It is not captured here: the run that produced this page had no embedding or
chat model, and its vector store held placeholder embeddings, which is enough for the metadata
lookup in turn 2 but would rank results meaninglessly.

## Turn 2: summarize that call

**User:** Summarize CALL-00421.

**Tool call:** `get_call_summary`

```json
{
  "call_id": "CALL-00421"
}
```

**Tool result (captured):** the lookup `get_call_summary` does first, `Retriever.get_by_id`, a
metadata filter on `call_id` and never a semantic search. The metadata carries the same ids the
OLAP star schema uses.

```json
{
  "call_id": "CALL-00421",
  "metadata": {
    "date": "2026-04-30T13:19:14",
    "call_id": "CALL-00421",
    "outcome": "resolved",
    "agent_id": "AGT-012",
    "category": "mortgage_inquiry",
    "member_id": "MBR-000124",
    "account_id": 654,
    "account_ids": [
      654
    ],
    "home_branch": "Downtown",
    "interaction_id": 421,
    "duration_seconds": 662
  },
  "text": "MEMBER: Can you give me an update on my mortgage? AGENT: Of course — the mortgage ending in 0654 carries a note rate of 6.587% with a current principal of $303,195.09. MEMBER: And the loan-to-value? My statement shows an LTV figure. AGENT: The LTV at origination was 88.87%, and your escrow balance is $984.60. MEMBER: That lines up with what I thought. AGENT: Anything else on the mortgage I can check for you?"
}
```

**Answer:**

> **ILLUSTRATIVE, not captured output.** Written by hand to show the shape of the answer from what the transcript says; the wording and the retrieved set depend on the chat and embedding models. To capture it, run:
> `uv run python -c "from ccai_mcp.tools import get_call_summary; print(get_call_summary('CALL-00421'))"`
>
> A member asked about current loan rates for something comparable to an account ending 0531. The agent quoted a note rate of 6.587% and the existing principal balance of $303,195.09, then offered a pre-qualification. Outcome: unresolved.

Cross-check, direct SQL on the marts (not a tool): `marts.d_member` says member
124 is Mary Miller; the member's mortgage snapshot has `mortgage_note_rate`
6.587 and `mortgage_principal_balance` 303,195.09. The rate
is in the transcript above, and so
is the principal.

## Turn 3: did callers like it?

**User:** How did loan-inquiry callers rate us, and how many were unhappy?

**Tool call:** `query_csat`

```json
{
  "category": "loan_inquiry"
}
```

```
CSAT Summary (72 responses)
Average score: 3.97/5
Score distribution: {1: 4, 2: 3, 3: 16, 4: 17, 5: 32}
Sample comments:
  - Good experience overall.
  - Average service.
  - Agent went above and beyond.
  - Good experience overall.
  - Minor wait time but resolved quickly.
```

**Tool call:** `query_csat`

```json
{
  "category": "loan_inquiry",
  "max_score": 2
}
```

```
CSAT Summary (7 responses)
Average score: 1.43/5
Score distribution: {1: 4, 2: 3, 3: 0, 4: 0, 5: 0}
Sample comments:
  - Problem was not resolved.
  - Problem was not resolved.
  - Not satisfied with the help I received.
  - Took too long.
  - Very frustrated with the outcome.
```

**Answer:** 72 loan-inquiry survey responses average 3.97 out of 5.
7 of them scored 2 or lower (4 ones, 3 twos).
Mary Miller's CALL-00421 scored 5 (`marts.f_csat`, interaction
421).
`query_csat` returns the aggregate and a handful of comments, not per-call rows; it reads the
Postgres `f_csat` fact, and CSAT is not in the vector store, so this cannot be combined with a
semantic transcript search.

## Turn 4: "what is our average interest rate?"

**User:** What is our average interest rate?

**Tool call:** `ask_the_analyst`

```json
{
  "question": "what is our average interest rate?",
  "decimals": 4
}
```

**Scripted step:** the model maps the question to declared metrics. Here it returns all
7 candidates listed for "rate" in `agent/data/ambiguous_terms.yml`.
The catalog lookup, the MetricFlow query and the SQL below ran for real.

```
Question: "what is our average interest rate?"
Resolved to 7 declared metric(s) — there is no single blended number; each word maps to N distinct, auditable metrics:

- average_mortgage_note_rate — Average interest rate members PAY on first-lien mortgages (paid, fixed). "interest rate" meaning 1 of the collision.
- weighted_mortgage_portfolio_rate — Mortgage note rate weighted by outstanding principal — the "what does our book actually cost" number, a RATIO (note_rate x balance) / balance, not a plain average. Numerator and denominator are declared metrics (note_rate_times_balance / mortgage_principal_balance), each lob-filtered.
- average_heloc_current_rate — Average HELOC effective rate = base_rate + margin (variable, two source columns). "interest rate" meaning 2.
- average_credit_card_purchase_apr — Average PURCHASE APR on cards — deliberately NOT the cash-advance APR on the same card. "interest rate" meaning 4a.
- average_credit_card_cash_advance_apr — Average cash-advance APR on cards. "interest rate" meaning 4b — a different rate on the same piece of plastic.
- average_deposit_apy — Average rate members EARN on deposits (sign-flipped vs loans). "interest rate" meaning 5.
- average_investment_return_pct — Average investment return — explicitly NOT interest. "interest rate" meaning 6, which is not interest at all.

MetricFlow result (7 metric(s)):
  average_mortgage_note_rate: 6.5888
  weighted_mortgage_portfolio_rate: 6.5695
  average_heloc_current_rate: 10.1101
  average_credit_card_purchase_apr: 18.1192
  average_credit_card_cash_advance_apr: 24.4280
  average_deposit_apy: 1.9373
  average_investment_return_pct: 7.4710
```

**Answer:** There is no single "interest rate": the catalog declares no metric by that name, so
it resolves to 7 distinct, line-of-business metrics.

| Metric | Value |
|---|---|
| `average_mortgage_note_rate` (Avg Mortgage Note Rate) | 6.5888 |
| `weighted_mortgage_portfolio_rate` (Weighted Mortgage Portfolio Rate) | 6.5695 |
| `average_heloc_current_rate` (Avg HELOC Current Rate) | 10.1101 |
| `average_credit_card_purchase_apr` (Avg Credit Card Purchase APR) | 18.1192 |
| `average_credit_card_cash_advance_apr` (Avg Credit Card Cash Advance APR) | 24.4280 |
| `average_deposit_apy` (Avg Deposit APY) | 1.9373 |
| `average_investment_return_pct` (Avg Investment Return %) | 7.4710 |

<details>
<summary>SQL generated by MetricFlow</summary>

```sql
WITH sma_10001_cte AS (
  SELECT
    product_id AS product
    , mortgage_note_rate AS __average_mortgage_note_rate
    , mortgage_note_rate * mortgage_principal_balance AS __note_rate_times_balance
    , heloc_current_rate AS __average_heloc_current_rate
    , purchase_apr AS __average_credit_card_purchase_apr
    , cash_advance_apr AS __average_credit_card_cash_advance_apr
    , deposit_apy AS __average_deposit_apy
    , investment_return_pct AS __average_investment_return_pct
    , mortgage_principal_balance AS __mortgage_principal_balance
  FROM "contactcenter"."marts"."f_account_snapshot" account_snapshot_src_10000
)

, rss_10007_cte AS (
  SELECT
    lob
    , product_id AS product
  FROM "contactcenter"."marts"."d_product" products_src_10000
)

SELECT
  MAX(subq_9.average_mortgage_note_rate) AS average_mortgage_note_rate
  , MAX(subq_19.weighted_mortgage_portfolio_rate) AS weighted_mortgage_portfolio_rate
  , MAX(subq_28.average_heloc_current_rate) AS average_heloc_current_rate
  , MAX(subq_37.average_credit_card_purchase_apr) AS average_credit_card_purchase_apr
  , MAX(subq_37.average_credit_card_cash_advance_apr) AS average_credit_card_cash_advance_apr
  , MAX(subq_46.average_deposit_apy) AS average_deposit_apy
  , MAX(subq_55.average_investment_return_pct) AS average_investment_return_pct
FROM (
  SELECT
    AVG(average_mortgage_note_rate) AS average_mortgage_note_rate
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__average_mortgage_note_rate AS average_mortgage_note_rate
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_5
  WHERE product__lob = 'mortgage'
) subq_9
CROSS JOIN (
  SELECT
    CAST(SUM(note_rate_times_balance) AS DOUBLE PRECISION) / CAST(NULLIF(SUM(mortgage_principal_balance), 0) AS DOUBLE PRECISION) AS weighted_mortgage_portfolio_rate
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__note_rate_times_balance AS note_rate_times_balance
      , sma_10001_cte.__mortgage_principal_balance AS mortgage_principal_balance
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_14
  WHERE product__lob = 'mortgage'
) subq_19
CROSS JOIN (
  SELECT
    AVG(average_heloc_current_rate) AS average_heloc_current_rate
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__average_heloc_current_rate AS average_heloc_current_rate
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_24
  WHERE product__lob = 'home'
) subq_28
CROSS JOIN (
  SELECT
    AVG(average_credit_card_purchase_apr) AS average_credit_card_purchase_apr
    , AVG(average_credit_card_cash_advance_apr) AS average_credit_card_cash_advance_apr
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__average_credit_card_purchase_apr AS average_credit_card_purchase_apr
      , sma_10001_cte.__average_credit_card_cash_advance_apr AS average_credit_card_cash_advance_apr
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_33
  WHERE product__lob = 'cards'
) subq_37
CROSS JOIN (
  SELECT
    AVG(average_deposit_apy) AS average_deposit_apy
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__average_deposit_apy AS average_deposit_apy
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_42
  WHERE product__lob = 'banking'
) subq_46
CROSS JOIN (
  SELECT
    AVG(average_investment_return_pct) AS average_investment_return_pct
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__average_investment_return_pct AS average_investment_return_pct
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_51
  WHERE product__lob = 'investments'
) subq_55
```

</details>

Each is filtered to its own `product__lob`, so the average mortgage note rate
(6.5888) is not blended with card APR
(18.1192) or deposit APY
(1.9373).

## Turn 5: the agent asks instead of guessing

**User:** What is our average interest rate?

The same question as turn 4, through the LangGraph agent. "rate" is an ambiguous term in
`agent/data/ambiguous_terms.yml`, so instead of running all seven metrics the graph pauses at
its only `interrupt()` and asks.

**Scripted step:** the classifier returns the `resolve_metric` route. The interrupt below is
what the graph returned.

**Interrupt payload** (`clarify_metric`, version 1):

```json
{
  "kind": "clarify_metric",
  "version": 1,
  "term": "rate",
  "prompt": "\"rate\" matches 7 declared metrics. Which do you mean?",
  "options": [
    {
      "id": "average_mortgage_note_rate",
      "label": "Average Mortgage Note Rate"
    },
    {
      "id": "weighted_mortgage_portfolio_rate",
      "label": "Weighted Mortgage Portfolio Rate"
    },
    {
      "id": "average_heloc_current_rate",
      "label": "Average Heloc Current Rate"
    },
    {
      "id": "average_credit_card_purchase_apr",
      "label": "Average Credit Card Purchase Apr"
    },
    {
      "id": "average_credit_card_cash_advance_apr",
      "label": "Average Credit Card Cash Advance Apr"
    },
    {
      "id": "average_deposit_apy",
      "label": "Average Deposit Apy"
    },
    {
      "id": "average_investment_return_pct",
      "label": "Average Investment Return Pct"
    }
  ],
  "multi_select": true
}
```

**User:** (picks the two mortgage metrics)

**Resume:** `Command(resume=...)` on the same `thread_id`, with this value:

```json
{
  "choices": [
    "average_mortgage_note_rate",
    "weighted_mortgage_portfolio_rate"
  ]
}
```

**Answer** (graph output, `route` = `resolve_metric`, `grounded` =
`true`, metrics `average_mortgage_note_rate, weighted_mortgage_portfolio_rate`):

```
MetricFlow result (2 metric(s)):
  average_mortgage_note_rate: 6.588841176470588
  weighted_mortgage_portfolio_rate: 6.569519350411952
```

<details>
<summary>SQL attached to the answer</summary>

```sql
WITH sma_10001_cte AS (
  SELECT
    product_id AS product
    , mortgage_note_rate AS __average_mortgage_note_rate
    , mortgage_note_rate * mortgage_principal_balance AS __note_rate_times_balance
    , mortgage_principal_balance AS __mortgage_principal_balance
  FROM "contactcenter"."marts"."f_account_snapshot" account_snapshot_src_10000
)

, rss_10007_cte AS (
  SELECT
    lob
    , product_id AS product
  FROM "contactcenter"."marts"."d_product" products_src_10000
)

SELECT
  MAX(subq_9.average_mortgage_note_rate) AS average_mortgage_note_rate
  , MAX(subq_19.weighted_mortgage_portfolio_rate) AS weighted_mortgage_portfolio_rate
FROM (
  SELECT
    AVG(average_mortgage_note_rate) AS average_mortgage_note_rate
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__average_mortgage_note_rate AS average_mortgage_note_rate
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_5
  WHERE product__lob = 'mortgage'
) subq_9
CROSS JOIN (
  SELECT
    CAST(SUM(note_rate_times_balance) AS DOUBLE PRECISION) / CAST(NULLIF(SUM(mortgage_principal_balance), 0) AS DOUBLE PRECISION) AS weighted_mortgage_portfolio_rate
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__note_rate_times_balance AS note_rate_times_balance
      , sma_10001_cte.__mortgage_principal_balance AS mortgage_principal_balance
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_14
  WHERE product__lob = 'mortgage'
) subq_19
```

</details>

The two numbers differ because one is a plain average over mortgage accounts and the other is
weighted by principal. The graph attaches the SQL and marks the answer grounded only when a
declared metric and its SQL are both present.

## Turn 6: "balance", and net liquidity

**User:** The call-center agent read Mary Miller a balance of $214,081.25 on
CALL-00260. Which balance is that, and what is our net member liquidity?

The transcript, from the same metadata lookup as turn 2:

```
MEMBER: Hello, could you tell me what my account balance is right now? AGENT: Certainly. May I have your member number so I can verify you? MEMBER: It's MBR-000207. AGENT: Thank you, Ryan Smith. The account ending in 1157 has a outstanding balance of $568.29. MEMBER: Does online banking show the same figure? AGENT: It should. Your available credit at the moment is $233.42.
```

**Tool call:** `query_metric`

```json
{
  "metrics": [
    "banking_available_balance",
    "banking_ledger_balance",
    "credit_card_outstanding",
    "net_member_liquidity"
  ],
  "decimals": 2
}
```

```
MetricFlow result (4 metric(s)):
  banking_available_balance: 38287038.69
  banking_ledger_balance: 38419236.37
  credit_card_outstanding: 2354869.83
  net_member_liquidity: 35932168.86
```

**Answer:** "balance" is not one metric: the catalog declares `banking_available_balance`
(holds and pending excluded), `banking_ledger_balance`, `credit_card_outstanding` (a
liability) and others. Across all members:

| Metric | Value |
|---|---|
| `banking_available_balance` | 38,287,038.69 |
| `banking_ledger_balance` | 38,419,236.37 |
| `credit_card_outstanding` | 2,354,869.83 |
| `net_member_liquidity` | 35,932,168.86 |

`net_member_liquidity` is the one declared blend, `banking_available_balance -
credit_card_outstanding` (38,287,038.69 - 2,354,869.83
= 35,932,168.86). Only the semantic layer knows the sign conventions; a
raw `SUM(balance)` would add a liability to assets.

<details>
<summary>SQL generated by MetricFlow</summary>

```sql
WITH sma_10001_cte AS (
  SELECT
    product_id AS product
    , banking_available_balance AS __banking_available_balance
    , banking_ledger_balance AS __banking_ledger_balance
    , card_outstanding_balance AS __credit_card_outstanding
  FROM "contactcenter"."marts"."f_account_snapshot" account_snapshot_src_10000
)

, rss_10007_cte AS (
  SELECT
    lob
    , product_id AS product
  FROM "contactcenter"."marts"."d_product" products_src_10000
)

, cm_6_cte AS (
  SELECT
    SUM(credit_card_outstanding) AS credit_card_outstanding
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__credit_card_outstanding AS credit_card_outstanding
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_14
  WHERE product__lob = 'cards'
)

SELECT
  MAX(subq_9.banking_available_balance) AS banking_available_balance
  , MAX(subq_9.banking_ledger_balance) AS banking_ledger_balance
  , MAX(cm_6_cte.credit_card_outstanding) AS credit_card_outstanding
  , MAX(subq_30.net_member_liquidity) AS net_member_liquidity
FROM (
  SELECT
    SUM(banking_available_balance) AS banking_available_balance
    , SUM(banking_ledger_balance) AS banking_ledger_balance
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__banking_available_balance AS banking_available_balance
      , sma_10001_cte.__banking_ledger_balance AS banking_ledger_balance
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_5
  WHERE product__lob = 'banking'
) subq_9
CROSS JOIN
  cm_6_cte
CROSS JOIN (
  SELECT
    banking_available_balance - credit_card_outstanding AS net_member_liquidity
  FROM (
    SELECT
      MAX(subq_27.banking_available_balance) AS banking_available_balance
      , MAX(cm_6_cte.credit_card_outstanding) AS credit_card_outstanding
    FROM (
      SELECT
        SUM(banking_available_balance) AS banking_available_balance
      FROM (
        SELECT
          rss_10007_cte.lob AS product__lob
          , sma_10001_cte.__banking_available_balance AS banking_available_balance
        FROM sma_10001_cte
        LEFT OUTER JOIN
          rss_10007_cte
        ON
          sma_10001_cte.product = rss_10007_cte.product
      ) subq_23
      WHERE product__lob = 'banking'
    ) subq_27
    CROSS JOIN
      cm_6_cte
  ) subq_29
) subq_30
```

</details>

Cross-check, direct SQL on the marts (not a tool): Mary Miller's banking account has
`banking_ledger_balance` 214,081.25 and `banking_available_balance`
214,028.01. The call-center agent's figure in CALL-00260 does not match this account's ledger balance ($214,081.25).

## Turn 7: LCV or LTV?

**User:** What's our LTV? Marketing keeps calling it LCV.

**Tool call:** `query_metric`

```json
{
  "metrics": [
    "loan_to_value",
    "member_lifetime_value"
  ],
  "decimals": 4
}
```

```
MetricFlow result (2 metric(s)):
  loan_to_value: 77.6274
  member_lifetime_value: 638890.6175
```

**Answer:** The two words are different metrics with different meanings.

| Metric | Value | What it is |
|---|---|---|
| `loan_to_value` | 77.6274 | Loan-to-value at origination (underwriting RISK) — a fact stored on the mortgage account, averaged. |
| `member_lifetime_value` | 638890.6175 | Lifetime customer value (marketing) — a DERIVED, PARAMETERIZED CONVENTION, not a fact. |

The 24 in `relationship_revenue * 24 / active_members` is the declared convention (the catalog description explains it), stated
once in the metric instead of re-invented per dashboard. It is in the SQL below.

<details>
<summary>SQL generated by MetricFlow</summary>

```sql
WITH sma_10005_cte AS (
  SELECT
    fee_revenue_amount AS __total_fee_revenue
    , interest_earned_amount AS __total_interest_income
    , member_id AS __active_members
  FROM "contactcenter"."marts"."f_transaction" transactions_src_10000
)

SELECT
  MAX(subq_9.loan_to_value) AS loan_to_value
  , MAX(subq_23.member_lifetime_value) AS member_lifetime_value
FROM (
  SELECT
    AVG(loan_to_value) AS loan_to_value
  FROM (
    SELECT
      products_src_10000.lob AS product__lob
      , account_snapshot_src_10000.ltv_at_origination AS loan_to_value
    FROM "contactcenter"."marts"."f_account_snapshot" account_snapshot_src_10000
    LEFT OUTER JOIN
      "contactcenter"."marts"."d_product" products_src_10000
    ON
      account_snapshot_src_10000.product_id = products_src_10000.product_id
  ) subq_5
  WHERE product__lob = 'mortgage'
) subq_9
CROSS JOIN (
  SELECT
    relationship_revenue * 24 / active_members AS member_lifetime_value
  FROM (
    SELECT
      MAX(subq_16.relationship_revenue) AS relationship_revenue
      , MAX(subq_21.active_members) AS active_members
    FROM (
      SELECT
        total_fee_revenue + total_interest_income AS relationship_revenue
      FROM (
        SELECT
          SUM(__total_fee_revenue) AS total_fee_revenue
          , SUM(__total_interest_income) AS total_interest_income
        FROM sma_10005_cte
      ) subq_15
    ) subq_16
    CROSS JOIN (
      SELECT
        COUNT(DISTINCT __active_members) AS active_members
      FROM sma_10005_cte
    ) subq_21
  ) subq_22
) subq_23
```

</details>

For scale, Mary Miller's mortgage was written at `ltv_at_origination`
88.87 (direct SQL, not a tool), against the portfolio average above.

## Turn 8: why did the number move?

**User:** Why did satisfaction on fee calls drop after the fee schedule change?

**Tool call:** `explain_change`. The agent route reads the question into these inputs (the
metric from "satisfaction", the category from "fee calls", the date from the change log); here
they are scripted.

```json
{
  "metric": "csat_average_score",
  "filters": {
    "category": [
      "fee_dispute"
    ]
  },
  "change_point": "2026-04-15",
  "question": "Why did satisfaction on fee calls drop after the fee schedule change?"
}
```

**Tool result / Answer:** the tool's own text. Values, windows and counts come from MetricFlow
and the vector store's `count`; the driver labels and quotes are written by the chat model, and
the tool keeps a quote only if it appears verbatim in the call it cites.

```
Change analysis: CSAT Average Score (category = fee_dispute)
Change point: 2026-04-15, from the question: the logged change "Fee schedule change".

Before [2026-03-04, 2026-04-15): 3.80 (n=10 responses)
After [2026-04-15, 2026-05-27): 3.13 (n=45 responses)
Change: -0.67 points
Weekly after the change: 2026-04-13: 3.60 (n=5); 2026-04-20: 2.90 (n=10); 2026-04-27: 3.14 (n=7); 2026-05-04: 3.67 (n=3); 2026-05-11: 3.00 (n=7); 2026-05-18: 3.00 (n=9); 2026-05-25: 3.00 (n=8)

Drivers in the calls after the change (63 calls in the after window):
- Difficulty in understanding or verifying the fee change: 1 of 63 calls in the after window; 1 in the before window. Members reported difficulty in understanding or verifying the fee change, including confusion about the notice process and the application of the new fee.
    CALL-00387: "It is waived when you keep a minimum balance of $4,643.14."
- not matched to a driver: 62

What the data could not determine:
- csat_average_score counts the 45 survey responses, which cover only some of the 63 calls after the change; drivers describe all those calls.
- Driver labels come from a language model that reads the calls; the counts are exact for its assignments, and quotes are checked against the call text.
- Weekly values are whole ISO weeks, so the week holding the change point also holds days before it.
- The timing fits the change, but the data cannot show that the change caused the shift.
```

<details>
<summary>SQL generated by MetricFlow, both windows</summary>

```sql
-- window [2026-03-04, 2026-04-15)
SELECT
  AVG(csat_average_score) AS csat_average_score
  , SUM(responses) AS responses
FROM (
  SELECT
    interactions_src_10000.category_code AS interaction__category_code
    , subq_4.__csat_average_score AS csat_average_score
    , subq_4.__responses AS responses
  FROM (
    SELECT
      interaction_id AS interaction
      , score AS __csat_average_score
      , 1 AS __responses
    FROM "contactcenter"."marts"."f_csat" csat_surveys_src_10000
    WHERE DATE_TRUNC('day', surveyed_at) BETWEEN '2026-03-04' AND '2026-04-14'
  ) subq_4
  LEFT OUTER JOIN
    "contactcenter"."marts"."f_interaction" interactions_src_10000
  ON
    subq_4.interaction = interactions_src_10000.interaction_id
) subq_9
WHERE interaction__category_code IN ('fee_dispute')

-- window [2026-04-15, 2026-05-27)
SELECT
  AVG(csat_average_score) AS csat_average_score
  , SUM(responses) AS responses
FROM (
  SELECT
    interactions_src_10000.category_code AS interaction__category_code
    , subq_4.__csat_average_score AS csat_average_score
    , subq_4.__responses AS responses
  FROM (
    SELECT
      interaction_id AS interaction
      , score AS __csat_average_score
      , 1 AS __responses
    FROM "contactcenter"."marts"."f_csat" csat_surveys_src_10000
    WHERE DATE_TRUNC('day', surveyed_at) BETWEEN '2026-04-15' AND '2026-05-26'
  ) subq_4
  LEFT OUTER JOIN
    "contactcenter"."marts"."f_interaction" interactions_src_10000
  ON
    subq_4.interaction = interactions_src_10000.interaction_id
) subq_9
WHERE interaction__category_code IN ('fee_dispute')
```

</details>

The tool's result carries a second block, `CHANGE RESULT (JSON, v1)`, which the agent reads
instead of parsing the text. It lists the call ids behind each driver, so a client can open any
cited call with `get_call_summary`.

## Turn 9: "What do we make on fees?"

**User:** What do we make on fees?

"fee" is an ambiguous term in `agent/data/ambiguous_terms.yml`: the catalog declares one revenue
metric per fee type, and a total. The graph pauses at its only `interrupt()` instead of summing
across them.

**Scripted step:** the classifier returns the `resolve_metric` route. The interrupt below is
what the graph returned.

**Interrupt payload** (`clarify_metric`, version 1):

```json
{
  "kind": "clarify_metric",
  "version": 1,
  "term": "fee",
  "prompt": "\"fee\" matches 5 declared metrics. Which do you mean?",
  "options": [
    {
      "id": "overdraft_fee_revenue",
      "label": "Overdraft Fee Revenue"
    },
    {
      "id": "nsf_fee_revenue",
      "label": "Nsf Fee Revenue"
    },
    {
      "id": "monthly_maintenance_fee_revenue",
      "label": "Monthly Maintenance Fee Revenue"
    },
    {
      "id": "atm_fee_revenue",
      "label": "Atm Fee Revenue"
    },
    {
      "id": "total_fee_revenue",
      "label": "Total Fee Revenue"
    }
  ],
  "multi_select": true
}
```

**User:** (picks overdraft fees)

**Resume:** `Command(resume=...)` on the same `thread_id`, with this value:

```json
{
  "choices": [
    "overdraft_fee_revenue"
  ]
}
```

**Answer** (graph output, `route` = `resolve_metric`, `grounded` =
`true`, metrics `overdraft_fee_revenue`):

```
MetricFlow result (1 metric(s)):
overdraft_fee_revenue: 32358.0
```

<details>
<summary>SQL attached to the answer</summary>

```sql
SELECT
  SUM(overdraft_fee_revenue) AS overdraft_fee_revenue
FROM (
  SELECT
    fee_type AS transaction__fee_type
    , fee_revenue_amount AS overdraft_fee_revenue
  FROM "contactcenter"."marts"."f_transaction" transactions_src_10000
) subq_2
WHERE transaction__fee_type = 'overdraft'
```

</details>

---

## What was and was not captured

- **Captured:** the lookups and every metric, CSAT and SQL figure above, the clarify interrupt
  payloads and the graph's resumed answers, the `explain_change` result of turn 8, and the
  cross-check SQL.
- **Scripted:** the user's messages, which tool is called, the classifier's route, the metric
  names `ask_the_analyst` resolves to, and the inputs turn 8 passes to `explain_change`.
- **Model-written, captured:** the driver labels and the choice of quotes in turn 8. They come
  from the chat model that ran (`LLM_PROVIDER`), so another model words them differently; the
  counts, windows and SQL do not depend on it.
- **Not captured:** `search_transcripts` and the written summary in `get_call_summary`, which
  need a chat model and real embeddings. Each says so where it appears.
