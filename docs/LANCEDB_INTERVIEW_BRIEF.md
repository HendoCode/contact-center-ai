# LanceDB interview brief

A one-page prep sheet for a LanceDB conversation about this repo. Every number comes from
`results/retrieval/2026-10-03_bench-pgvector-lancedb-x1.json`. Every claim points at the file
that backs it.

## 1. The two-minute pitch

Call-center transcripts get retrieved through one `Retriever` interface (`retrieval/base.py`)
with two interchangeable stores: pgvector and LanceDB (`retrieval/lancedb_backend.py`).

- **Embedded:** LanceDB runs in-process (no server or Compose service) on the Lance columnar
  format. It stores the vector next to the filterable columns (`category`, `outcome`, `date`)
  and the text in one table.
- **Three search modes in one engine:** vector (cosine), native full-text search (BM25 via the
  `FTS()` index), and hybrid, which fuses the two with `RRFReranker` (K=60).
- **Prefiltering:** every mode applies the metadata filter before top-k (`prefilter=True`), so a
  selective filter still returns k hits.
- **Comparison point:** pgvector is the baseline. The R3 bench (`retrieval/bench.py`) runs both
  stores on the same embeddings and the same 40 labeled queries.
- **Same data, read by DuckDB:** the dataset LanceDB writes is read in place by DuckDB's `lance`
  extension with no export step (`retrieval/duckdb_lance.py`, `retrieval/README.md`).
- **Cloud:** the `az://` path has been exercised against the Azurite emulator only, not real ADLS
  yet (`make lance-azure-check`, `retrieval/README.md` "LanceDB on az://").

## 2. The numbers (x1)

Setup: 1,250 calls (1,063 unique texts) and 40 labeled queries (11 topical, 10 filter, 10 exact,
9 paraphrase), k=10, 3 latency passes. Embeddings are `nomic-embed-text` on CPU Ollama. It ran on
one 8-core laptop (i5-1038NG7, 15 GB), with lancedb 0.39.0, Postgres 16 and pgvector 0.8.7.
Latency is `search()` only: embeddings are precomputed, and embedding a query alone has a p50 of
42.9 ms, more than any search below.

| store/mode | recall@5 | recall@10 | MRR@10 | p50 ms | p95 ms | ingest s |
|---|---|---|---|---|---|---|
| pgvector/vector | 0.845 | 0.863 | 0.833 | 17.8 | 20.1 | 2.39 |
| lancedb/vector | 0.845 | 0.863 | 0.833 | 13.4 | 16.8 | 3.54 (index build 0.10) |
| lancedb/fts | 0.700 | 0.710 | 0.694 | 9.4 | 11.2 | same table |
| lancedb/hybrid | 0.857 | **0.900** | **0.890** | 15.3 | 19.6 | same table |

recall@10 by query type:

| mode | topical | filter | exact | paraphrase |
|---|---|---|---|---|
| vector (both stores) | 1.000 | 0.953 | 0.600 | 0.889 |
| fts | 0.864 | 0.890 | 1.000 | **0.000** |
| hybrid | 0.945 | 0.965 | 0.947 | 0.722 (MRR 0.889) |

- x10 scale (12,500 rows): **TBD, pending the captain's run**.
- x80 scale (100,000 rows, the first size where IVF-PQ is built): **TBD, pending the captain's run**.

Caveats: synthetic data, one machine, one run at x1, and CPU embeddings. recall@k is capped
recall, |relevant in top k| / min(k, |relevant|).

## 3. Design choices and honest tradeoffs

- **No vector index below 100,000 rows** (`VECTOR_INDEX_MIN_ROWS`). A flat scan over about 1,000
  vectors is exact and takes milliseconds. IVF-PQ costs training and recall. That is why vector
  recall is identical on both stores: both do exact search on the same vectors.
- **pgvector ties on quality** and ingests faster at this size (2.4 s vs 3.5 s). LanceDB's
  ingest includes `merge_insert` plus building the FTS and `call_id` indexes. LanceDB is about
  4 ms faster at p50 here: it runs in-process, while pgvector is client-server.
- **Keyword-only fails on paraphrase** (recall 0.0) but is perfect on exact identifiers (1.0).
  Vector is the reverse (exact 0.6).
- **Hybrid with RRF is the best overall** (recall@10 0.90, MRR 0.89). It lifts exact from 0.6 to
  0.95, but paraphrase recall@10 drops from 0.89 to 0.72, because the FTS list adds noise there.
  RRF uses ranks only, so cosine and BM25 never need score calibration.
- **pgvector stays vector-only in this repo**. FTS and hybrid return "not supported" there, so
  the comparison is not dressed up.

## 4. Likely questions

1. **Why LanceDB over a vector server?** It's embedded, has no ops, and keeps one table for
   vectors, text, metadata, FTS and hybrid. See `retrieval/lancedb_backend.py` docstring.
2. **Why no ANN index?** Exact search wins below about 100k rows, and PQ training needs data.
   See `VECTOR_INDEX_MIN_ROWS` and the `--scale` path in `retrieval/bench.py`.
3. **How does filtering interact with top-k?** It's a prefilter, so k hits survive selective
   filters. See `where(flt, prefilter=True)` and the filter query type, recall 0.95–0.97.
4. **How do you fuse BM25 and cosine?** RRF, K=60, rank-based. See `RRFReranker` and the score
   normalisation in the docstring.
5. **Where does hybrid lose?** On paraphrase recall@10 (0.72 vs 0.89 for vector). See the
   per-type table above.
6. **How are re-ingests idempotent?** `merge_insert` on `call_id`, plus a BTree index. See
   `retrieval/lancedb_backend.py`.
7. **How did you make the benchmark fair?** The same embeddings are cached once, the same labels
   are used, there's a warm-up query, and recall is capped. See the `retrieval/bench.py`
   docstring and `retrieval/labels/README.md`.
8. **Can other engines read the data?** Yes: DuckDB reads the `.lance` dataset directly, with
   gaps in its filter and fusion support. See `retrieval/README.md` "DuckDB".
9. **What changes at scale?** IVF-PQ at 100k rows, compaction with `optimize()`, and object
   storage. See the `--scale 80` path, and the x10/x80 placeholders above.
10. **How would you run this on cloud storage?** `az://` with Entra via `az`, no account keys.
    See `retrieval/lance_azure_check.py`, Azurite-verified only.

## 5. Limits: what not to overclaim

- **Cloud:** `az://` is verified on the Azurite emulator only. Real ADLS (Entra auth, latency) has
  not been run yet (`make lance-azure-check`).
- **Scale:** there are no multi-node, concurrency or large-scale claims. x1 is 1,250 rows, and the
  x10/x80 results are pending.
- **Data:** synthetic transcripts and labels derived from generator facts. These are not
  production relevance judgments.
- **Measurement:** latency comes from one laptop and excludes embedding time. Ingest timings are
  single runs.

Sources checked on 2026-10-03:
- [LanceDB RRF reranker](https://docs.lancedb.com/integrations/reranking/rrf): K defaults to 60,
  and RRF is the default reranker for hybrid search.
- The `lancedb` version comes from `uv.lock` and the results JSON.
