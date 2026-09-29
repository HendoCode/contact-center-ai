-- Agent/specialist/supervisor dimension; snowflakes to d_team via team_id.

select
    staff_id,
    full_name,
    team_id,
    role,
    hire_date
from {{ source('oltp', 'staff') }}