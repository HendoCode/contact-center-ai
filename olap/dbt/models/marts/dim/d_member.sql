-- SCD-less member dimension (nightly rebuild; slowly-changing handling is a
-- later phase). Business *contacts* are `member_type='individual'` rows linked
-- to their business entity via `business_id` (self-FK); the business entity
-- itself is a `member_type='business'` row with no household.

select
    member_id,
    household_id,
    member_type,
    legal_name,
    email,
    phone,
    joined_date,
    credit_score_band,
    residence_state,
    business_id,
    naics_code,
    employee_count,
    -- branch the member is served by; null on a raw load that predates the column
    {% if has_source_column('oltp', 'member', 'home_branch') %}home_branch{% else %}cast(null as {{ dbt.type_string() }}) as home_branch{% endif %}
from {{ source('oltp', 'member') }}