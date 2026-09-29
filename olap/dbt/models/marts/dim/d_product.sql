-- The LOB discriminator lives here. d_account snowflakes to this dimension,
-- and `product__lob` is the filter that disambiguates every colliding metric.

select
    product_id,
    lob,
    product_code,
    product_name,
    is_active
from {{ source('oltp', 'product') }}