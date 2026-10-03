# LanceDB interview brief

A prep sheet for a LanceDB conversation about this repo. The numbers come from the R3 bench
runs in `results/retrieval/` (x1: `2026-10-03_bench-pgvector-lancedb-x1.json`; x80:
`2026-10-03_bench-pgvector-lancedb-x80.json` and `2026-10-03_x80-ivf-pq.json`) and the
`make lance-azure-check` run. Every claim points at the file that backs it.

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
- **Cloud:** the same code runs on `az://` (ADLS Gen2) with Entra auth via `az login`, no account
  keys; verified on real Azure storage with identical recall (`make lance-azure-check`).

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

Caveats: synthetic data, one machine, one run at x1, and CPU embeddings. recall@k is capped
recall, |relevant in top k| / min(k, |relevant|).

## 2b. Scale (x10, x80) and the cloud path

**How to read scale runs.** `--scale N` repeats the 1,250 calls N times with new ids. All copies
count as relevant, so label recall at scale mostly measures which copies come back, not index
quality. The real measures at scale are **ingest, index build, latency, and `ann_recall@10`**: the
overlap of a backend's top 10 with an exact brute-force top 10 (`retrieval/bench.py`
`ExactReference`). At x80 each copy carries a small seeded jitter, so the vectors are near-duplicates
rather than identical points.

**x10 (12,500 rows; both stores exact, no ANN index; two runs, shown as ranges).** Ingest: pgvector
19.4–20.3 s vs LanceDB 3.8–5.3 s (index build 0.4 s). Vector p50: pgvector 81–82 ms vs LanceDB
65–75 ms; p95 86.6 vs 89.5 ms. LanceDB fts p50 9–11 ms, hybrid p50 54–63 ms (recall@10 0.812). Vector
recall@10: pgvector 0.805, LanceDB 0.800, identical across runs. At this size query latency is roughly tied, and
ingest is the clear gap.

**x80 (100,000 rows, 768-dim, one CPU laptop, pgvector 0.8 in Docker, Lance on local disk).** The
exact scan is the reference.

| config | recall@10 | ann_recall@10 | p50 ms | p95 ms | ingest s | index build s |
|---|---|---|---|---|---|---|
| pgvector exact scan | 0.800 | 1.000 | 884 | 909 | 179.5 | none |
| pgvector HNSW (m16, efc64), ef_search 40 / 100 / 200 | 0.725 | 0.478 / 0.545 / 0.703 | 4.1 / 5.3 / 7.4 | ~86–90 | 179.5 | 28.6 |
| LanceDB ivf_flat (316 partitions) | 0.800 | 1.000 | 55.0 | 58.1 | 65.5 | 32.1 |
| LanceDB IVF-PQ (default, 316 partitions) | 0.440 | 0.025 | 16.0 | 27.6 | 146.4 | 115.4 |
| LanceDB IVF-PQ, refine_factor 20 | 0.528 | 0.163 | 20.3 | n/a | same | same |

LanceDB, other modes at x80: fts p50 11.7 ms (recall 0.675). Hybrid with ivf_flat: recall@10 0.812,
MRR 0.875, p50 57.1 ms. Hybrid with IVF-PQ: recall 0.608, p50 19.5 ms. Filter queries, p50:
pgvector exact 78 ms, pgvector HNSW 74 ms, LanceDB ivf_flat 29 ms, hybrid 35 ms. The PQ run's
pgvector HNSW row was 0.700 / ann 0.495 / 3.8 ms, consistent with run A.

`nprobes` (20 to 200) did not change IVF-PQ results. It is applied (the query plan shows it), but each
query's near-duplicate copies share one partition, so the loss is PQ compression, not probing.

**az:// on real Azure storage** (Entra via `az login`, from a laptop on a home network to an ADLS
account): recall@10 identical to local disk (vector 0.863, fts 0.710, hybrid 0.900). p50 local vs
az: vector 13.6 vs 400 ms, fts 9.3 vs 188 ms, hybrid 15.5 vs 407 ms; ingest 3.3 vs 4.2 s. The gap is
the home-network round trip to object storage, not Lance on Azure with compute in the same region.
That colocated run is the next step and is untested.

## 3. Design choices and honest tradeoffs

- **At x80 these are different points on the speed/accuracy dial, not one winner.**
  - **pgvector HNSW** is the fastest per query (4 ms), but approximate: ann 0.48–0.70, with defaults not
    tuned beyond ef_search.
  - **LanceDB ivf_flat** returns exactly the exact-scan results (ann 1.000) at 55 ms, about 16x
    faster than the scan. It ingests about 2.7x faster than pgvector and is about 2.5x faster on
    filtered queries (prefilter).
  - **Default IVF-PQ** is the compressed, small-index end: recall 0.44 and ann 0.025 on this
    duplicate-heavy corpus.

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
   storage. See the x80 table in section 2b.
10. **How would you run this on cloud storage?** `az://` with Entra via `az`, no account keys.
    See `retrieval/lance_azure_check.py`: verified on real ADLS, identical recall.

11. **Did you tune the index?** Yes, and that tuning was the experiment: `nprobes` 20 to 200
    (no effect: near-duplicates share a partition), `refine_factor` (PQ ann 0.025 to 0.163), and
    index type (ivf_flat gives ann 1.000). PQ compression was the limiter. See
    `retrieval/README.md`, "Why nprobes did not move recall".
12. **So is LanceDB faster than pgvector?** Not per query: HNSW is faster (4 ms vs 55 ms) but
    approximate. The LanceDB story here is exact results at a fraction of a scan's cost, 2.5x faster
    filtered queries, 2.7x faster ingest, native FTS and hybrid, and no server. See the x80 table.

## 5. Limits: what not to overclaim

- **Cloud:** `az://` is verified on real ADLS with Entra auth, but only from a home network.
  Latency with compute in the same region is untested.
- **Scale:** x80 is 100,000 synthetic rows: replicated texts with jittered vectors. ann_recall is harsh on near-duplicates.
  It was one run per config at x80 and two at x10, on one machine. There are no multi-node,
  concurrency or larger-scale claims.
- **Data:** synthetic transcripts and labels derived from generator facts. These are not
  production relevance judgments.
- **Measurement:** latency comes from one laptop and excludes embedding time. Ingest timings are
  single runs.

Sources checked on 2026-10-03:
- [LanceDB RRF reranker](https://docs.lancedb.com/integrations/reranking/rrf): K defaults to 60,
  and RRF is the default reranker for hybrid search.
- The `lancedb` version comes from `uv.lock` and the results JSON.
