-- Conformed calendar, one row per day. Spans 2006-01-01 .. 2026-12-31 to
-- cover the earliest member.joined_date (2006) through the latest
-- rate_lock.expires_at (2026-10-31). Also feeds metricflow_time_spine.
--
-- Portable across Postgres, Snowflake and Databricks: the spine comes from
-- dbt.date_spine (its end date is exclusive in practice, hence 2027-01-01) and every
-- calendar attribute is built from dbt cross-database macros, ANSI extract() and
-- case expressions. No to_char / isodow / doy / generate_series, whose names and
-- semantics differ per warehouse (Snowflake's week start and DAYNAME depend on
-- session parameters; to_char has no portable 'Day'/'Month' pattern).

with spine as (
    {{ dbt.date_spine(
        datepart='day',
        start_date="cast('2006-01-01' as date)",
        end_date="cast('2027-01-01' as date)"
    ) }}
),

days as (
    select
        cast(date_day as date) as date_day,
        -- ISO day of week, 1=Mon .. 7=Sun. 1900-01-01 was a Monday, so the day
        -- offset from it modulo 7 is the Monday-based weekday on every warehouse.
        cast(
            mod({{ dbt.datediff("cast('1900-01-01' as date)", 'cast(date_day as date)', 'day') }}, 7) + 1
            as {{ dbt.type_int() }}
        ) as day_of_week_iso
    from spine
),

iso_week as (
    select
        date_day,
        day_of_week_iso,
        -- The ISO week belongs to the year of its Thursday; its number is the
        -- (1-based) 7-day block that Thursday falls in.
        cast({{ dbt.dateadd('day', '4 - day_of_week_iso', 'date_day') }} as date) as week_thursday
    from days
)

select
    cast(
        extract(year from date_day) * 10000
        + extract(month from date_day) * 100
        + extract(day from date_day)
        as {{ dbt.type_int() }}
    )                                           as date_key,
    date_day,
    day_of_week_iso,                                                     -- 1=Mon .. 7=Sun
    case day_of_week_iso
        when 1 then 'Monday'
        when 2 then 'Tuesday'
        when 3 then 'Wednesday'
        when 4 then 'Thursday'
        when 5 then 'Friday'
        when 6 then 'Saturday'
        else 'Sunday'
    end                                         as day_name,
    cast(extract(day from date_day) as {{ dbt.type_int() }}) as day_of_month,
    cast(
        {{ dbt.datediff(dbt.date_trunc('year', 'date_day'), 'date_day', 'day') }} + 1
        as {{ dbt.type_int() }}
    )                                           as day_of_year,
    cast({{ dbt.dateadd('day', '1 - day_of_week_iso', 'date_day') }} as date) as week_start_date,
    cast({{ dbt.dateadd('day', '7 - day_of_week_iso', 'date_day') }} as date) as week_end_date,
    cast(
        floor(
            (
                {{ dbt.datediff(dbt.date_trunc('year', 'week_thursday'), 'week_thursday', 'day') }}
            ) / 7.0
        ) + 1
        as {{ dbt.type_int() }}
    )                                           as week_of_year,
    cast({{ dbt.date_trunc('month', 'date_day') }} as date) as month_start_date,
    cast(extract(month from date_day) as {{ dbt.type_int() }}) as month_number,
    case cast(extract(month from date_day) as {{ dbt.type_int() }})
        when 1 then 'January'
        when 2 then 'February'
        when 3 then 'March'
        when 4 then 'April'
        when 5 then 'May'
        when 6 then 'June'
        when 7 then 'July'
        when 8 then 'August'
        when 9 then 'September'
        when 10 then 'October'
        when 11 then 'November'
        else 'December'
    end                                         as month_name,
    cast({{ dbt.date_trunc('quarter', 'date_day') }} as date) as quarter_start_date,
    cast(extract(quarter from date_day) as {{ dbt.type_int() }}) as quarter_number,
    cast({{ dbt.date_trunc('year', 'date_day') }} as date) as year_start_date,
    cast(extract(year from date_day) as {{ dbt.type_int() }}) as year_number,
    case
        when day_of_week_iso in (6, 7) then true
        else false
    end                                         as is_weekend
from iso_week
