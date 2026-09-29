-- Bridge table at interaction x account grain. This is where the classic
-- member/account double-count ambiguity is resolved: a single interaction can
-- be "about" more than one account (~20% of calls), so counting interactions
-- must divide by this grain or use DISTINCT interaction_id, never a naive
-- join of two fact tables.

select
    ia.interaction_id,
    ia.account_id,
    ia.account_role,
    i.member_id,
    i.staff_id,
    i.category_code,
    i.started_at,
    i.outcome
from {{ source('oltp', 'interaction_account') }} as ia
join {{ source('oltp', 'interaction') }} as i
    on i.interaction_id = ia.interaction_id