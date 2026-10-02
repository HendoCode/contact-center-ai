-- Degenerate of-day dimension: 24 one-hour buckets for slicing call volume
-- by time of day. Kept at hour granularity; finer buckets can be added.
--
-- dbt.generate_series yields 1..24 (a `generated_number` column) on every warehouse,
-- so the hour is that minus one. Casts and concatenation go through dbt macros.

with hours as (
    select
        cast(generated_number - 1 as {{ dbt.type_int() }}) as hour_of_day
    from (
        {{ dbt.generate_series(24) }}
    ) as series
)

select
    hour_of_day                                                   as time_key,
    hour_of_day,
    {{ dbt.concat([
        "lpad(cast(hour_of_day as " ~ dbt.type_string() ~ "), 2, '0')",
        "':00-'",
        "lpad(cast(hour_of_day + 1 as " ~ dbt.type_string() ~ "), 2, '0')",
        "':00'",
    ]) }}                                                         as hour_bucket,
    case
        when hour_of_day < 6  then 'Night'
        when hour_of_day < 12 then 'Morning'
        when hour_of_day < 18 then 'Afternoon'
        else 'Evening'
    end                                                           as time_of_day_bucket
from hours
