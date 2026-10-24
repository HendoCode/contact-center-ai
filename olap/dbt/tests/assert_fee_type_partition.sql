-- Fee types partition the fee ledger: a line has a fee_type exactly when it is a
-- fee, so the four per-type revenue metrics plus `other` add up to total_fee_revenue.
-- Any returned row breaks that.

select transaction_id, txn_type, fee_type
from {{ ref('f_transaction') }}
where (txn_type = 'fee') <> (fee_type is not null)
