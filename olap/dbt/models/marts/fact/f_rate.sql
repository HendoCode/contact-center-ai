-- Posted (product-level) rate fact, grain = 1 product_rate row. This is the
-- THIRD meaning of "interest rate" (a posted/teaser rate), distinct from the
-- account-level rates in f_account_snapshot.

select
    pr.product_rate_id,
    pr.product_id,
    p.lob,
    pr.effective_date,
    pr.rate_type,
    pr.rate_value,
    pr.is_teaser
from {{ source('oltp', 'product_rate') }} as pr
join {{ source('oltp', 'product') }} as p
    on p.product_id = pr.product_id