# data

- Postgres Flexible Server 16, `B_Standard_B1ms`, no HA, with `azure.extensions = VECTOR` so `CREATE EXTENSION vector` works. Firewall: Azure services plus the operator IP (private networking is a follow-up).
- ADLS Gen2 account with `lance` and `dbt-artifacts` filesystems; the app identity and operator get `Storage Blob Data Contributor`.

The generated admin password is in the env's remote state. `database_url` is a sensitive output.

After apply, enable the extension once: `psql "$DATABASE_URL" -c 'CREATE EXTENSION IF NOT EXISTS vector'`, then `make seed && make ingest` against it.
