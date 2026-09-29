-- Interaction category. NB: category != lob — a card call can be a fraud call,
-- so lob_id (the loose link to a LOB) is frequently NULL. The real LOB
-- discriminator for account metrics lives on d_product, not here.

select
    category_code,
    category_name,
    lob_id
from {{ source('oltp', 'interaction_category') }}