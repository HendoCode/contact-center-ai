-- Degenerate dimension for interaction.channel (phone/chat/secure_message/branch).

select distinct
    channel as channel_code,
    channel as channel_name
from {{ source('oltp', 'interaction') }}
order by channel