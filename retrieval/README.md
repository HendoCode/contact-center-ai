# retrieval

One `Retriever` interface (`base.py`) over two stores: `pgvector_backend.py` and
`lancedb_backend.py`. `bench.py` is the R3 benchmark (`make bench`); its tables live in
`results/retrieval/`. This README only records the DuckDB check.

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
- Only a local `LANCE_URI` was tried; `az://` / `s3://` are untested.
- `INSTALL` downloads the extension, so the tests that use it are `integration`-marked:

```bash
uv run pytest tests/retrieval/test_duckdb_lance.py -m integration   # needs network once
```
