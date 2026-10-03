# data

- Postgres Flexible Server 16, `B_Standard_B1ms`, no HA, with `azure.extensions = VECTOR` so `CREATE EXTENSION vector` works. Firewall: Azure services plus the operator IP (private networking is a follow-up).
- ADLS Gen2 account with `lance` and `dbt-artifacts` filesystems; the app identity and operator get `Storage Blob Data Contributor`.

The generated admin password is in the env's remote state. `database_url` is a sensitive output.

After apply, run `make seed && make ingest` against it. The first ingest creates the `vector` extension itself (`rag/embeddings.py` builds a `PGVector` store, which runs `CREATE EXTENSION IF NOT EXISTS vector` by default), so no manual step is expected. This has not been verified against Azure Postgres. Only if that first ingest reports the extension missing, run `psql "$DATABASE_URL" -c 'CREATE EXTENSION IF NOT EXISTS vector'` once and ingest again.
