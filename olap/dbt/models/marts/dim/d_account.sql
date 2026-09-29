-- Conformed account dimension, snowflaked to d_product (via product_id).
--
-- Per §6 of the demo plan, the per-LOB account columns are de-normalized into
-- d_account as lob-qualified, null-per-type columns (note_rate, purchase_apr,
-- deposit_apy, investment_market_value, ...). Null-per-type is acceptable here
-- precisely because every metric filters by product.lob: a mortgage row has
-- note_rate and nothing in purchase_apr, and no metric is allowed to blend the
-- two. f_account_snapshot projects these same columns into measures.

select
    account_id,
    account_number,
    opened_date,
    status,
    product_id,
    lob,
    product_code,
    product_name,
    is_active,
    primary_member_id,
    opened_by_staff_id,

    -- Per-LOB descriptive attributes (member-visible type info)
    banking_sub_type,
    inv_type,
    coverage_code,
    property_state,
    term_months,
    draw_period_months,

    -- Per-LOB measure columns (lob-qualified; see data-dictionary.md for the
    -- "which balance/rate/limit" ambiguity notes)
    original_principal,
    current_principal,
    note_rate,
    monthly_pi,
    escrow_balance,
    ltv_at_origination,
    heloc_credit_limit,
    heloc_drawn_balance,
    heloc_base_rate,
    heloc_margin,
    insurance_annual_premium,
    insurance_monthly_premium,
    insurance_deductible,
    banking_ledger_balance,
    banking_available_balance,
    deposit_apy,
    monthly_fee,
    waiver_min_balance,
    card_credit_limit,
    card_outstanding_balance,
    card_available_credit,
    purchase_apr,
    cash_advance_apr,
    rewards_rate,
    investment_market_value,
    cost_basis,
    investment_cash_value,
    investment_return_pct,
    advisory_fee_bps
from {{ ref('stg_account_lob') }}