-- Wide per-line-of-business account row (ephemeral: compiled as a CTE, never
-- stored). This is the polymorphic OLTP parent (`account`) left-joined to its
-- six Table-Per-Type children, with `product.lob` as the runtime discriminator.
--
-- The point of the demo: there is deliberately NO generic `balance_` or
-- `rate_` column here. Each LOB stores the same spoken word under its own,
-- LOB-qualified column name and sign convention, so the semantic layer is
-- forced to disambiguate "balance"/"interest rate"/"limit" by `lob`.

with account_core as (
    select
        a.account_id,
        a.product_id,
        a.primary_member_id,
        a.account_number,
        a.opened_date,
        a.status,
        a.opened_by_staff_id,
        p.lob,
        p.product_code,
        p.product_name,
        p.is_active
    from {{ source('oltp', 'account') }} as a
    join {{ source('oltp', 'product') }} as p
        on a.product_id = p.product_id
)

select
    c.account_id,
    c.product_id,
    c.primary_member_id,
    c.account_number,
    c.opened_date,
    c.status,
    c.opened_by_staff_id,
    c.lob,
    c.product_code,
    c.product_name,
    c.is_active,

    -- mortgage (first-lien): "balance" meaning 1, "rate" meaning 1, LTV fact
    m.original_principal,
    m.current_principal,
    m.note_rate,
    m.term_months,
    m.monthly_pi,
    m.escrow_balance,
    m.ltv_at_origination,
    m.property_state,

    -- home equity (HELOC): "limit" meaning 1, "balance" meaning 3, "rate" 2
    h.credit_limit                          as heloc_credit_limit,
    h.drawn_balance                         as heloc_drawn_balance,
    h.base_rate                             as heloc_base_rate,
    h.margin                                as heloc_margin,
    h.draw_period_months,

    -- car insurance: "premium" (money) and "limit" meaning 2 (coverage)
    i.annual_premium                        as insurance_annual_premium,
    i.monthly_premium                       as insurance_monthly_premium,
    i.coverage_code,
    i.deductible                            as insurance_deductible,
    i.vehicle_year,

    -- banking: "balance" 4a/4b, "rate" meaning 3 (APY, earned not paid)
    b.sub_type                              as banking_sub_type,
    b.ledger_balance                        as banking_ledger_balance,
    b.available_balance                     as banking_available_balance,
    b.apy                                   as deposit_apy,
    b.monthly_fee,
    b.waiver_min_balance,

    -- credit card: "limit" meaning 3, "balance" 5 (LIABILITY), "rate" 4/5
    cc.credit_limit                         as card_credit_limit,
    cc.outstanding_balance                  as card_outstanding_balance,
    cc.available_credit                     as card_available_credit,
    cc.purchase_apr,
    cc.cash_advance_apr,
    cc.rewards_rate,

    -- investments: "balance" 6/7, "return" (NOT interest)
    inv.inv_type,
    inv.market_value                        as investment_market_value,
    inv.cost_basis,
    inv.cash_value                          as investment_cash_value,
    inv.ytd_return_pct                      as investment_return_pct,
    inv.advisory_fee_bps

from account_core as c
left join {{ source('oltp', 'mortgage_account') }} as m
    on c.account_id = m.account_id
left join {{ source('oltp', 'home_equity_account') }} as h
    on c.account_id = h.account_id
left join {{ source('oltp', 'auto_insurance_policy') }} as i
    on c.account_id = i.account_id
left join {{ source('oltp', 'banking_account') }} as b
    on c.account_id = b.account_id
left join {{ source('oltp', 'credit_card_account') }} as cc
    on c.account_id = cc.account_id
left join {{ source('oltp', 'investment_account') }} as inv
    on c.account_id = inv.account_id