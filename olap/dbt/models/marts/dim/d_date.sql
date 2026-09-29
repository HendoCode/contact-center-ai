-- Conformed calendar, one row per day. Spans 2006-01-01 .. 2026-12-31 to
-- cover the earliest member.joined_date (2006) through the latest
-- rate_lock.expires_at (2026-10-31). Also feeds metricflow_time_spine.

with spine as (
    select generate_series(
        date '2006-01-01',
        date '2026-12-31',
        interval '1 day'
    )::date as date_day
)

select
    cast(to_char(date_day, 'YYYYMMDD') as int) as date_key,
    date_day,
    extract(isodow from date_day)::int         as day_of_week_iso,  -- 1=Mon .. 7=Sun
    trim(to_char(date_day, 'Day'))             as day_name,
    extract(day from date_day)::int            as day_of_month,
    extract(doy from date_day)::int            as day_of_year,
    date_trunc('week', date_day)::date         as week_start_date,
    (date_trunc('week', date_day)::date + interval '6 days')::date as week_end_date,
    extract(week from date_day)::int           as week_of_year,
    date_trunc('month', date_day)::date        as month_start_date,
    extract(month from date_day)::int          as month_number,
    trim(to_char(date_day, 'Month'))           as month_name,
    date_trunc('quarter', date_day)::date      as quarter_start_date,
    extract(quarter from date_day)::int        as quarter_number,
    date_trunc('year', date_day)::date         as year_start_date,
    extract(year from date_day)::int           as year_number,
    case
        when extract(isodow from date_day)::int in (6, 7) then true
        else false
    end                                        as is_weekend
from spine