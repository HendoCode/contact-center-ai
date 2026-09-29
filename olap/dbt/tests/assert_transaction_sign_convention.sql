-- Sign-convention check: the OLTP seed credits only `deposit` and `interest`;
-- every other txn_type is a debit. `interest` is left free on purpose — it is
-- the one type that is genuinely signed both ways (earned vs charged), even
-- though this seed only emits the earned (positive) leg. Any returned row is a
-- violation that would corrupt a blended "total amount" number.

select *
from {{ ref('f_transaction') }}
where (txn_type = 'deposit' and amount < 0)
   or (txn_type in ('withdrawal', 'payment', 'purchase', 'fee', 'premium', 'transfer')
       and amount > 0)