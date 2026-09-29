select
    household_id,
    household_label,
    income_band
from {{ source('oltp', 'household') }}