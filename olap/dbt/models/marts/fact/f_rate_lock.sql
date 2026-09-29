-- Mortgage/home rate-lock pipeline fact, grain = 1 lock. "lock" here means a
-- rate lock (financing event), NOT a fraud account freeze (an interaction
-- outcome) — the two are kept in different facts on purpose.

select
    rl.lock_id,
    rl.member_id,
    rl.product_id,
    p.lob,
    rl.locked_rate,
    rl.loan_amount,
    rl.locked_at,
    rl.expires_at,
    rl.lock_period_days,
    rl.status,
    rl.locked_at::date                     as lock_date,
    case when rl.status = 'exercised' then 1 else 0 end as is_exercised,
    case when rl.status = 'expired'   then 1 else 0 end as is_expired,
    case when rl.status = 'active'    then 1 else 0 end as is_active,
    case when rl.status = 'cancelled' then 1 else 0 end as is_cancelled
from {{ source('oltp', 'rate_lock') }} as rl
join {{ source('oltp', 'product') }} as p
    on p.product_id = rl.product_id