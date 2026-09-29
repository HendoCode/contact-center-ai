-- Sign-convention check: every lob-qualified balance in f_account_snapshot is
-- stored as a NON-NEGATIVE magnitude. The asset-vs-liability sign (checking is
-- an asset, card outstanding is a liability) is deliberately NOT encoded in a
-- sign bit on the column: it lives in the declared metrics, so a naive
-- `SUM(balance)` has no generic column to blend. Any negative magnitude here
-- (or a negative escrow/limit/premium) is a seed or join error.

select *
from {{ ref('f_account_snapshot') }}
where mortgage_principal_balance < 0
   or escrow_balance < 0
   or heloc_drawn_balance < 0
   or heloc_credit_limit < 0
   or banking_ledger_balance < 0
   or banking_available_balance < 0
   or card_outstanding_balance < 0
   or card_credit_limit < 0
   or card_available_credit < 0
   or investment_market_value < 0
   or investment_cash_value < 0
   or insurance_annual_premium < 0
   or insurance_deductible < 0