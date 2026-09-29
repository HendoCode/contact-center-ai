-- Degenerate of-day dimension: 24 one-hour buckets for slicing call volume
-- by time of day. Kept at hour granularity; finer buckets can be added.

with hours as (
    select generate_series(0, 23) as hour_of_day
)

select
    hour_of_day                                                   as time_key,
    hour_of_day,
    lpad(hour_of_day::text, 2, '0') || ':00-'
        || lpad((hour_of_day + 1)::text, 2, '0') || ':00'          as hour_bucket,
    case
        when hour_of_day < 6  then 'Night'
        when hour_of_day < 12 then 'Morning'
        when hour_of_day < 18 then 'Afternoon'
        else 'Evening'
    end                                                            as time_of_day_bucket
from hours