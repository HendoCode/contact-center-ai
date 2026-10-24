-- Ledger flow fact, grain = 1 transaction (25,000 rows). Signed amounts:
-- credits are +, debits are - (the OLTP seed only credits `deposit` and
-- `interest`; everything else is a debit). The flow components below are what
-- make "balance" mean stock-vs-flow, and they feed the LCV convention.

select
    t.transaction_id,
    t.account_id,
    a.primary_member_id                                   as member_id,
    a.product_id,
    p.lob,
    t.posted_at,
    cast(t.posted_at as date)                                 as transaction_date,
    t.amount,
    t.txn_type,
    t.status,
    t.description,

    -- which fee a 'fee' line is; null on other lines. Uses the raw column when the load
    -- has it, otherwise reads the posting description.
    {% if has_source_column('oltp', 'account_transaction', 'fee_type') %}
    case when t.txn_type = 'fee' then t.fee_type end
    {% else %}
    case
        when t.txn_type <> 'fee' then null
        when lower(t.description) like '%overdraft%' then 'overdraft'
        when lower(t.description) like '%maintenance%' then 'monthly_maintenance'
        when lower(t.description) like '%nsf%' then 'nsf'
        when lower(t.description) like '%atm%' then 'atm'
        else 'other'
    end
    {% endif %}
        as fee_type,

    -- fee revenue to the CU (stored as a debit -> negate to a positive amount)
    case when t.txn_type = 'fee' then -t.amount else 0 end
        as fee_revenue_amount,
    -- interest earned by the member (credit) vs interest charged (debit)
    case when t.txn_type = 'interest' and t.amount > 0 then t.amount else 0 end
        as interest_earned_amount,
    case when t.txn_type = 'interest' and t.amount < 0 then -t.amount else 0 end
        as interest_paid_amount,
    -- premiums paid by the member (stored as a debit -> positive magnitude)
    case when t.txn_type = 'premium' then -t.amount else 0 end
        as premium_paid_amount

from {{ source('oltp', 'account_transaction') }} as t
join {{ source('oltp', 'account') }} as a
    on a.account_id = t.account_id
join {{ source('oltp', 'product') }} as p
    on p.product_id = a.product_id