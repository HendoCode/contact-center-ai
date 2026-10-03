-- Degenerate dimension for account.status (open/closed/delinquent/charge_off).
-- "Delinquency" is a derived ratio over this dimension, never a column.

select distinct
    status as account_status_code,
    status as account_status_name
from {{ source('oltp', 'account') }}
order by account_status_code