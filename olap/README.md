# OLTP foundation — Meridian Valley Credit Union

Phase 1 of the semantic-layer demo: the PostgreSQL schema, the deterministic
synthetic seed generator, and the loader. This layer is **additive** — it does
not touch `ccai_mcp/` or `rag/`, and `transcripts.json` / `csat.json` keep
their exact shapes so RAG ingest and `query_csat()` work unchanged.

## Quick start

```bash
# 1. Start the db (pgvector/pgvector:pg16)
docker compose up -d db

# 2. Apply the schema (21 tables; idempotent)
olap/oltp/apply.sh

# 3. Generate deterministic seed JSON (seed 42; bit-identical across runs)
python data/synthetic/generate_data.py

# 4. Load into Postgres (idempotent upserts — no duplicates on re-run)
python olap/seed.py
```

`apply.sh` targets the docker-compose `db` service first and falls back to
`psql $DATABASE_URL` (default `postgresql://postgres:postgres@localhost:5432/contactcenter`)
when docker isn't running, so it also works against any Postgres.

## What's here

| Path | Purpose |
|---|---|
| `oltp/schema.sql` | Full DDL: named PKs/FKs/CHECKs, Table-Per-Type account polymorphism, both junction tables, `product_rate`, `rate_lock` |
| `oltp/apply.sh` | Applies `schema.sql` to the `db` service (or `$DATABASE_URL`) |
| `seed.py` | Deterministic JSON → Postgres, idempotent upserts |
| `docs/erd.md` | Mermaid ERD + cardinality notes |
| `docs/data-dictionary.md` | One line per table + the LOB-ambiguity catalog |

## Row-count plan (seed 42, deterministic)

| Entity | Rows |
|---|---|
| households | 150 |
| members | 700 (550 individual + 40 business + 110 contacts) |
| teams / staff | 8 / 60 |
| products | 24 (3–5 per LOB) |
| accounts | **1,400** (banking 530 · cards 340 · mortgage 170 · home 110 · insurance 140 · investments 110) |
| interactions | **1,250** |
| interaction_accounts | ~1,388 (≈80/20 single/multi-account) |
| csat | ~945 (75%) |
| transactions | **25,000** |
| product_rates | 126 |
| rate_locks | 500 |

## The deliberately-built ambiguity

The transcripts are **numerically bound to accounts**: dialogue templates
splice each caller's real `note_rate`, `ledger_balance`, `purchase_apr`,
`available_credit`, `apy`, `escrow_balance`, etc., so a supervisor asking the
RAG MCP and a BI user asking the semantic layer get **the same number from
different engines**. See `docs/data-dictionary.md` for the full LOB-ambiguity
catalog, and `docs/erd.md` for the model.