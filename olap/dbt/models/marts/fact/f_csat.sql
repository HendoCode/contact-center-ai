-- CSAT survey fact, grain = 1 survey (~75% of interactions are surveyed).
-- NPS is a *derived ratio* (declared in metrics.yml), never a stored column;
-- this fact only carries the promoter/detractor flags it is built from.
-- Standard 5-point mapping: 5 = promoter, 4/3 = passive, 2/1 = detractor.

select
    c.survey_id,
    c.interaction_id,
    c.score,
    c.comment,
    c.surveyed_at,
    c.question_code,
    i.member_id,
    i.staff_id,
    s.team_id,
    i.category_code,
    i.started_at              as interaction_started_at,
    case when c.score = 5 then 1 else 0 end as is_promoter,
    case when c.score <= 2 then 1 else 0 end as is_detractor,
    case when c.score >= 4 then 1 else 0 end as is_satisfied
from {{ source('oltp', 'csat_survey') }} as c
join {{ source('oltp', 'interaction') }} as i
    on i.interaction_id = c.interaction_id
join {{ source('oltp', 'staff') }} as s
    on s.staff_id = i.staff_id