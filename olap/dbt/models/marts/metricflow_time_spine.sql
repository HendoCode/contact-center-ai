-- MetricFlow time spine: exactly one row per day at day granularity. The
-- dbt Semantic Layer / `mf` requires this to run metric_time queries. See the
-- `time_spine` config in _time_spine.yml.

select
    date_day
from {{ ref('d_date') }}