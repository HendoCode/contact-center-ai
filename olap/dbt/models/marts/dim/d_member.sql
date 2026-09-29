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
    employee_count
from {{ source('oltp', 'member') }}