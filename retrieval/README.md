# retrieval

One `Retriever` interface (`base.py`) over two stores: `pgvector_backend.py` and
`lancedb_backend.py`. `bench.py` is the R3 benchmark (`make bench`); its tables live in
`results/retrieval/`. This README records the bench prerequisites and the DuckDB check.

## Running the bench

`make bench` needs `make up` (Postgres for the pgvector backend; `ARGS="--backends lancedb"`
skips it) and an embedding provider. With `EMBEDDING_PROVIDER=ollama` it uses
`nomic-embed-text` and pulls it on first run if Ollama lacks it: about 270 MB in the `ollama`
volume, skipped once present. Every unique transcript (about 1,060) is embedded once up front,
with a progress line roughly every 10%. On a CPU-only Ollama that step is slow: about 6 minutes
on an 8-core laptop, about 45 minutes on 2 cores.

Reruns reuse the document embeddings cached under `.cache/bench-embeddings/` (gitignored, one file per
embedding model, keyed by text hash), so only the first run pays for the embedding step; `--no-cache` skips the
cache. Ingest goes in batches of `--ingest-batch` rows (default 12,500, the largest single insert proven at x10),
so `--scale 80` never sends 100,000 rows in one call. A backend or mode that fails becomes a one-line
`failed: <error>` row (at most 300 characters), the other backends still run, and the results file is
written either way.

## LanceDB on az:// (ADLS Gen2)

`make lance-azure-check` runs the bench's LanceDB half twice, on a local temp directory and on
`az://lance/<prefix>` in the envs/dev ADLS account, then prints recall@10 and p50 side by side.
Both stores share the same corpus, labels and embeddings (each unique document is embedded once), so the only
difference is the storage. The filter queries exercise the metadata prefilter. The Azure table is
dropped afterwards (`--keep` leaves it), and nothing is written to `results/`.

```bash
az login                                                   # the account must hold Storage Blob Data Contributor
make lance-azure-check ARGS="--dry-run"                    # the plan; touches nothing
make lance-azure-check                                     # account from AZURE_STORAGE_ACCOUNT_NAME or terraform output
```

**Auth.** Entra only; the account has shared keys off, and no account key is ever used.
`retrieval/lance_azure_check.py` passes `azure_storage_account_name` plus
`azure_use_azure_cli=true` as `storage_options`, so Lance's object store (the Rust `object_store`
crate) gets a storage token from `az` for the signed-in user. If that is refused, set
`AZURE_STORAGE_SAS_KEY` to a container-scoped SAS (keep it in 1Password, for example
`op://CMW/azure-ccai`) and rerun; the SAS is used instead.

**What the results mean.** Recall should match the local numbers exactly, because the data,
vectors and queries are the same. A difference would point at the storage path, not the
retrieval. Latency is higher on `az://` because every read is a network round trip; that gap is
the cost of object storage over local disk, not a recall tradeoff.

**Verified (2026-10-03):**

- The pinned `lancedb` 0.39.0 native library contains the Azure object-store options
  `azure_use_azure_cli`, `azure_storage_token`, `azure_storage_sas_key` and
  `azure_storage_use_emulator` (string check of `_lancedb.abi3.so`). The `object_store` Azure
  options, `AZURE_USE_AZURE_CLI` among them, are documented in
  [obstore's Azure store reference](https://developmentseed.org/obstore/v0.4.0/api/store/azure/),
  which wraps the same crate.
- A full run on `az://` against the Azurite emulator: ingest, vector, fts and hybrid,
  prefiltered queries and the table drop. The test suite's offline embeddings stood in for
  Ollama, so the absolute scores mean nothing, but recall@10 was identical on disk and on `az://`
  in every mode (`tests/retrieval/test_lance_azure_check.py`, `-m integration` with `AZURITE=1`).

**Not run, so not claimed:**

- **A real ADLS account:** Entra via `az` against a hierarchical-namespace (ADLS Gen2) account, the RBAC role in practice, real `az://` latency.
- **`DefaultAzureCredential`:** `object_store` has its own credential chain (CLI, managed identity, workload identity, client secret), not the Azure SDK's.

## DuckDB over the Lance dataset (R3 stretch)

`duckdb_lance.py` opens the dataset `LanceDBRetriever` writes (`<LANCE_URI>/<table>.lance`)
from DuckDB through the `lance` extension, read-only. `duckdb` is in the `lance` group
(`uv sync --extra dev --group lance`).

Checked on 2026-10-02, x86_64 Linux, Python 3.12: DuckDB 1.5.6, `lance` extension build
`2913169` (`INSTALL lance; LOAD lance` from the core extension repository), lancedb 0.39.0,
against the 50-document test fixture. No timing comparison was run, so there are no numbers.

**Works**
- Reads the table `LanceDBRetriever` wrote with no export step: `SELECT ... FROM '<dir>.lance'`.
- SQL aggregates: `count_by("category", "outcome")`, `count()`, any ad hoc SQL via `sql()`.
- `lance_vector_search`, `lance_fts`, `lance_hybrid_search`, returned as `Hit`s through
  `DuckDBLanceReader.search(query, k, where, mode)`; `get_by_id` is a filter lookup.
- Vector results agree with the LanceDB backend on the fixture (same top hit and top-5 set,
  same cosine score).

**Gaps vs the LanceDB backend**
- Read-only: no `ingest`, so it is not a full `Retriever` and is not selectable through
  `get_retriever()`.
- `where`: the extension's `filter` argument only works for namespace-backed tables and
  `lance_hybrid_search` has none. `vector` + `where` is an exact SQL scan with the filter
  applied first; `fts`/`hybrid` + `where` fetch every candidate and filter afterwards. Both
  are exact, neither scales like LanceDB's prefilter.
- Vector distance is L2 only (no cosine option); scores are recomputed as cosine, but the
  ranking equals cosine ranking only for unit-length embeddings.
- Hybrid fuses with an `alpha` blend, not RRF, so hybrid rankings and scores differ from the
  LanceDB backend's. It is not wired into `make bench` for that reason: a third column would
  compare different fusion methods.
- Only a local `LANCE_URI` was tried here; for `az://` see the section above, and `s3://` is untested.
- `INSTALL` downloads the extension, so the tests that use it are `integration`-marked:

```bash
uv run pytest tests/retrieval/test_duckdb_lance.py -m integration   # needs network once
```
