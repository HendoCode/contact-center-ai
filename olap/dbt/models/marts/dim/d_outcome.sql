-- Degenerate dimension for interaction.outcome.

select distinct
    outcome as outcome_code,
    outcome as outcome_name
from {{ source('oltp', 'interaction') }}
order by outcome