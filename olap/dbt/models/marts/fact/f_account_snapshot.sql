-- Account balance/rate snapshot, grain = account x snapshot_date (monthly).
--
-- The synthetic OLTP seed carries only the *current* as-of account state (it
-- has opened_date and live balances, not a balance history), so this build
-- materializes one month-end snapshot per account in place of a periodic
-- snapshot loader. The grain columns (account_id, snapshot_date) are real and
-- stable, so wiring in a dbt snapshot / daily loader later changes nothing
-- declarative downstream — every measure here is a lob-qualified column that
-- the semantic layer declares separately.

select
    a.account_id,
    a.primary_member_id                                   as member_id,
    a.product_id,
    a.lob,
    a.status,
    a.opened_date,
    date '2026-08-31'                                     as snapshot_date,

    -- Stocks (semi-additive "as of" balances; sign convention lives in the
    -- metrics, NOT in a shared "balance" column)
    a.current_principal                                   as mortgage_principal_balance,
    a.original_principal                                  as mortgage_original_principal,
    a.monthly_pi                                          as mortgage_monthly_pi,
    a.escrow_balance,
    a.heloc_drawn_balance,
    a.heloc_credit_limit,
    a.banking_ledger_balance,
    a.banking_available_balance,
    case when a.lob = 'banking' and a.banking_sub_type = 'checking'
        then a.banking_ledger_balance end                 as checking_ledger_balance,
    case when a.lob = 'banking' and a.banking_sub_type = 'checking'
        then a.banking_available_balance end              as checking_available_balance,
    case when a.lob = 'banking' and a.banking_sub_type = 'savings'
        then a.banking_ledger_balance end                 as savings_balance,
    a.card_outstanding_balance,                           -- liability sign
    a.card_credit_limit,
    a.card_available_credit,
    a.investment_market_value,
    a.investment_cash_value,
    a.insurance_annual_premium,
    a.insurance_monthly_premium,
    a.insurance_deductible,                               -- insurance "limit"

    -- Rates ("interest rate" meanings 1..5 + "return"; averaged per account)
    a.note_rate                                           as mortgage_note_rate,
    a.heloc_base_rate,
    a.heloc_margin,
    (a.heloc_base_rate + a.heloc_margin)                  as heloc_current_rate,
    a.purchase_apr,
    a.cash_advance_apr,
    a.deposit_apy,
    a.investment_return_pct,
    a.ltv_at_origination                                  -- LTV (facts; cf. LCV)

from {{ ref('d_account') }} as a