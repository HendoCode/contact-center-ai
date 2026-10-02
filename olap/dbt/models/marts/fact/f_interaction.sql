-- Contact fact, grain = 1 interaction. One row per contact (mostly phone).
-- team_id is denormalized from staff so "volume by team" is a single hop; the
-- account-many-to-many of "what was this call about" lives in the bridge
-- f_interaction_account, never here (no double-counting in this fact).

select
    i.interaction_id,
    i.member_id,
    i.staff_id,
    s.team_id,
    i.category_code,
    i.started_at,
    i.ended_at,
    i.duration_seconds,
    i.channel,
    i.direction,
    i.caller_type,
    i.outcome,
    i.first_contact_resolved,

    -- degenerate time-of-day bucket (join to d_time)
    cast(extract(hour from i.started_at) as {{ dbt.type_int() }}) as hour_of_day,
    cast(i.started_at as date)                                   as interaction_date
from {{ source('oltp', 'interaction') }} as i
join {{ source('oltp', 'staff') }} as s
    on s.staff_id = i.staff_id