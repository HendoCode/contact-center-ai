select
    team_id,
    team_name,
    function_area
from {{ source('oltp', 'team') }}