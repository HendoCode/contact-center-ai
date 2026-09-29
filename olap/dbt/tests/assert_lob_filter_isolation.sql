-- LOB-filter results: `product.lob` is the runtime discriminator, and each
-- per-LOB rate/balance column must be populated on exactly its own LOB and
-- NULL everywhere else. This is the invariant the semantic layer's
-- `Dimension('product__lob')` filters rely on: a campaign that asks for
-- "average interest rate" must not be able to blend note_rate into
-- purchase_apr. Any returned row breaks the isolation.

select *
from {{ ref('f_account_snapshot') }}
where
    -- populated on the wrong LOB (blend risk)
    (lob <> 'mortgage'     and (mortgage_note_rate is not null or ltv_at_origination is not null))
    or (lob <> 'cards'     and (purchase_apr is not null or cash_advance_apr is not null))
    or (lob <> 'banking'   and deposit_apy is not null)
    or (lob <> 'home'      and heloc_base_rate is not null)
    or (lob <> 'investments' and investment_return_pct is not null)
    -- missing on its own LOB (under-population)
    or (lob = 'mortgage'     and (mortgage_note_rate is null or mortgage_principal_balance is null))
    or (lob = 'cards'        and (purchase_apr is null or card_outstanding_balance is null))
    or (lob = 'banking'      and (banking_ledger_balance is null or banking_available_balance is null))
    or (lob = 'home'         and (heloc_base_rate is null or heloc_drawn_balance is null))
    or (lob = 'investments'  and (investment_market_value is null or investment_return_pct is null))
    or (lob = 'car_insurance' and insurance_annual_premium is null)