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

### Fair comparison at scale (`--scale 80`)

A plain `make bench ARGS="--scale 80"` now gives an approximate-vs-approximate comparison, plus the exact
reference:

| row | index | search |
|---|---|---|
| `pgvector/vector` | none (exact scan), the reference | exact |
| `pgvector-hnsw/vector` | HNSW, cosine, `m=16`, `ef_construction=64`, built after ingest (its own `index_build_s`) | `ef_search=40`, `iterative_scan=relaxed_order` |
| `lancedb/vector`, `/hybrid` | IVF-PQ, cosine, sqrt(rows) partitions at >= 100,000 rows, else flat | LanceDB defaults |

Every row records its `index` and `search` settings in the results JSON and table. The settings:

- **pgvector:** `--pgvector-index none` skips the HNSW variant. `--hnsw-m`, `--hnsw-ef-construction` and
  `--hnsw-ef-search` tune it. The HNSW index is a partial index over the bench collection on
  `embedding::vector(<dim>)`, because LangChain's column has no fixed dimension. It is queried with
  bench-only SQL through that expression and dropped afterwards.
- **LanceDB:** `--nprobes`, `--refine-factor` and `--num-partitions` set the IVF-PQ knobs.
- **LanceDB build knobs:** `--num-partitions` and `--num-sub-vectors` (PQ) change the index build.
- **Sweep:** `--sweep` takes `nprobes=...` or `refine_factor=...`. Repeat it to combine the two into a grid,
  for example `--sweep nprobes=20,50,100,200 --sweep refine_factor=1,20`. Each combination is scored on the
  same table, and the run ends with one compact table (recall@10, MRR@10, p50 and p95) that includes the
  pgvector rows for reference.
- **Shared memory:** the Compose `db` service sets `shm_size: 1gb`, because Docker's 64 MB `/dev/shm` is too
  small for a parallel HNSW build at 100,000 rows (`could not resize shared memory segment ... No space
  left on device`). With a small `/dev/shm`, `--pgvector-parallel-workers 0` builds serially; the failure
  row names both fixes.

**What scale runs measure.** Scale runs measure ingest, index build, latency and **`ann_recall@10`**.
Label recall is only meaningful at x1. `ann_recall@10` is the overlap of a vector row's top 10 with a
brute-force exact top 10 that the bench computes itself: the same jittered vectors, the same `where` filter,
and ties at the 10th score counted as hits. On replicated data, label recall mixes replica identity (which of
80 copies came back) with index quality. `ann_recall@10` measures only the index, and an exact scan scores
1.0 by construction.

**More knobs.**
- `--lance-index ivf_pq|ivf_flat|ivf_sq|ivf_hnsw_sq|ivf_hnsw_pq` picks the index type; those are the five
  types the pinned lancedb 0.39 ships.
- `--lance-num-sub-vectors` and `--lance-num-bits` set PQ. They apply to the `*_pq` types only.
- `--sweep ef_search=40,100,200` sweeps pgvector's HNSW on the same index. LanceDB's
  `nprobes`/`refine_factor` grid is swept separately.
- Each row also reports `p50_ms_by_type` and `p95_ms_by_type`.
- Timing starts after one untimed pass over every query.

**Why nprobes did not move recall.** It is applied: the plan shows `ANNIvfPartition ... minimum_nprobes=N,
maximum_nprobes=Some(N)`, and a test asserts it. With 80 near-identical replicas per document, a query's
neighbours sit in one partition, so extra probes find nothing new. On synthetic replicated data, overlap with
the exact top 10 stayed at 2/10 from 1 to all 316 probes. The loss is PQ quantization, which cannot rank
near-identical vectors. `refine_factor`, which re-ranks `k x refine_factor` candidates with exact
distances, or a non-PQ index (`ivf_flat`, `ivf_hnsw_sq`) addresses it.

**Why HNSW p95 >> p50.** The filter queries cause it. With `iterative_scan = relaxed_order`, a selective
`where` keeps walking the graph until k matches pass. In a local x80 run the HNSW p95 was 37.8 ms for filter
queries vs 1.1 ms for every other type. A cold cache is not the cause: timing follows a full warm-up pass.

**Replica jitter.** `--scale N` repeats the same texts with new ids. Identical vectors made k-means
degenerate (empty clusters, "many duplicate vectors"), so for `--scale > 1` each repeat now gets a seeded
jitter of norm `--jitter-eps` (default 0.05, cosine to the original about 0.999), renormalised. The first
copy and every query vector are unchanged, and `--scale 1` is unchanged. `--no-jitter` turns it off.
All replicas still count as relevant, so recall at scale measures ranking among near-duplicates: compare
ingest and latency across scales, not recall.

**What a local run showed.** These are offline hashed embeddings at x80, so the absolute scores mean nothing.
The `nprobes` sweep did not move LanceDB's recall (0.225 at 5, 20 and 50 probes), while `refine_factor=20`
lifted it to the exact-scan level (about 0.30–0.33 vs 0.300). That points at PQ compression rather than
probing as the x80 recall gap. Check it on real embeddings with `--sweep refine_factor=1,5,10,20`.

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
Blank `AZURE_*` values (a `.env` copied from `.env.example` has empty `AZURE_TENANT_ID`,
`AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET`) are dropped before connecting, and with `az login` auth so are
those three when they come from `.env`, so they cannot switch the store to a client-secret flow.
Embeddings come from the same `.cache/bench-embeddings/` cache as `make bench` (`--no-cache` skips it).

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
