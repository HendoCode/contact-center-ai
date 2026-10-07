WITH sma_10003_cte AS (
  SELECT
    1 AS __call_volume
    , case when first_contact_resolved then 1 else 0 end AS __first_contact_resolved_calls
  FROM "contactcenter"."marts"."f_interaction" interactions_src_10000
)

, sma_10001_cte AS (
  SELECT
    product_id AS product
    , mortgage_note_rate AS __average_mortgage_note_rate
    , banking_available_balance AS __banking_available_balance
    , card_outstanding_balance AS __credit_card_outstanding
  FROM "contactcenter"."marts"."f_account_snapshot" account_snapshot_src_10000
)

, rss_10007_cte AS (
  SELECT
    lob
    , product_id AS product
  FROM "contactcenter"."marts"."d_product" products_src_10000
)

SELECT
  MAX(subq_5.call_volume) AS call_volume
  , MAX(subq_15.average_mortgage_note_rate) AS average_mortgage_note_rate
  , MAX(subq_24.banking_available_balance) AS banking_available_balance
  , MAX(subq_33.credit_card_outstanding) AS credit_card_outstanding
  , MAX(subq_39.first_contact_resolution_rate) AS first_contact_resolution_rate
FROM (
  SELECT
    SUM(__call_volume) AS call_volume
  FROM sma_10003_cte
) subq_5
CROSS JOIN (
  SELECT
    AVG(average_mortgage_note_rate) AS average_mortgage_note_rate
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__average_mortgage_note_rate AS average_mortgage_note_rate
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_11
  WHERE product__lob = 'mortgage'
) subq_15
CROSS JOIN (
  SELECT
    SUM(banking_available_balance) AS banking_available_balance
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__banking_available_balance AS banking_available_balance
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_20
  WHERE product__lob = 'banking'
) subq_24
CROSS JOIN (
  SELECT
    SUM(credit_card_outstanding) AS credit_card_outstanding
  FROM (
    SELECT
      rss_10007_cte.lob AS product__lob
      , sma_10001_cte.__credit_card_outstanding AS credit_card_outstanding
    FROM sma_10001_cte
    LEFT OUTER JOIN
      rss_10007_cte
    ON
      sma_10001_cte.product = rss_10007_cte.product
  ) subq_29
  WHERE product__lob = 'cards'
) subq_33
CROSS JOIN (
  SELECT
    CAST(SUM(__first_contact_resolved_calls) AS DOUBLE PRECISION) / CAST(NULLIF(SUM(__call_volume), 0) AS DOUBLE PRECISION) AS first_contact_resolution_rate
  FROM sma_10003_cte
) subq_39