# contact-center-ai: agent hand-off (v1, Oct 1, 2026)

**For:** coding agents working on `HendoCode/contact-center-ai`, and the orchestrators dispatching them (firstmate on OpenRouter models; second-mate on the Anthropic subscription).
**Owner:** Stephen Henderson. He merges every PR, runs every command that costs money, and holds every credential.
**This file is public-safe.** Copy it into the repo as `docs/AGENT_HANDOFF.md`. It contains no client names, no account identifiers, and no personal context. Keep it that way when you edit it.

---

## 0. Read this first (every agent, every ticket)

1. Read the repo's `CLAUDE.md`, then this file, then only the files your ticket names.
2. Work in your own worktree on a branch named `t/<ticket-id>-<slug>`. One ticket per branch. Never push to `main`.
3. Do exactly the ticket. If you find other work worth doing, list it under "Follow-ups" in the PR. Don't do it.
4. If the ticket needs a decision this file doesn't make, **stop and ask** (write the question in the PR or the orchestrator inbox). Don't guess. If the verify commands fail three times running, stop and report what you saw.
5. You are done when every acceptance check passes **and you ran the verify commands yourself and pasted the output** into the PR. "Should work" is not done.

---

## 1. What this project is for

A single public portfolio repo on synthetic data. It shows one contact-center analytics system, built on one fictional credit union ("Meridian Valley," seed 42), across the stack a data/AI solutions architect gets asked about:

```
synthetic data (seed 42): transcripts, CSAT, OLTP ledger
   │
   ├── retrieval:  pgvector ⇄ LanceDB               (one Retriever interface, swap by env var)
   ├── semantics:  dbt + MetricFlow → Postgres | Snowflake | Databricks   (same models, three targets)
   ├── models:     OpenRouter/OpenAI | Anthropic | Fireworks (hosted + fine-tuned) | vLLM | Ollama
   └── agent:      LangGraph state graph → consumes the MCP server's tools → LangSmith traces + evals
infra: Docker Compose (local, no cloud bill) and Terraform on Azure (Snowflake stays on AWS; the stack is cross-cloud on purpose)
```

**Quality bar:** a skeptical engineer at any vendor in that diagram can clone the repo, run `make demo`, and watch it work, and a 20-minute screen-share holds up under their questions. That means: honest README, real numbers with dates and hardware, nothing claimed that the repo doesn't do. **Wide and shallow:** each piece ships on its own (README section, results table, git tag). No piece waits on another unless the dependency table says so.

---

## 2. The repo today (verified Oct 1 against `main`)

_History: a dated 2026-10-01 snapshot, kept as written. For current state see the [README](../README.md), [docs/TOUR.md](TOUR.md) and [results/](../results/)._

- `data/synthetic/generate_data.py`: seeded generator (`random.seed(42)`, `Faker.seed(42)`). Writes `transcripts.json`, `csat.json`, and the OLTP JSON files. `N_INTERACTIONS = 1250`.
- `olap/`: OLTP schema plus `seed.py` (idempotent JSON → Postgres); a dbt project with staging, a star schema (`d_*` dims, `f_*` facts), `semantic/semantic_models.yml` and `metrics.yml`, assertion tests, and a `profiles.yml` with a single Postgres target. The `dbt` dependency group pins `dbt-core==1.12.5`, `dbt-postgres==1.11.0`, `dbt-metricflow==0.15.0`.
- `rag/embeddings.py`: `get_embeddings()` (`EMBEDDING_PROVIDER` = openai | ollama) and `get_vector_store()` (LangChain `PGVector`). `rag/pipeline.py`: `get_llm()` (`LLM_PROVIDER` = openai-compatible, defaulting to OpenRouter, or ollama), `--ingest` and `--query`, `rag_query()`. **Ingest is not idempotent:** running it twice duplicates documents.
- `ccai_mcp/`: stdio MCP server with five tools: `search_transcripts`, `get_call_summary`, `query_csat`, `query_metric`, `ask_the_analyst`. The metric tools shell out to the `mf` CLI. `get_call_summary` uses a **metadata filter on `call_id`**, never a semantic lookup. Keep it that way.
- `tests/`: `test_config.py`, `test_metrics.py`, `test_server_handshake.py` (asserts the exact five tool names; needs no DB and no key).
- `docker-compose.yml`: `db` (pgvector pg16), `structurizr`, `ollama`, `open-webui`. There's a `#TODO` about restart policies.
- `infra/terraform/main.tf`: azurerm `~> 3.0`, App Service plus Postgres Flexible Server, Entra variables.
- `blogs/`: the "Anchoring AI" series. **Posts 01–04 are published** (GitHub Pages). 05–12 are placeholders or drafts. `blogs/build.sh` needs pandoc ≥ 3.3.
- No Makefile, no linter, no CI beyond the blog deploy workflow.

**Known drift to fix in T00:**
- `CLAUDE.md` says transcripts are "150 calls"; the generator says 1,250 interactions. Check what `transcripts.json` actually holds and correct the doc.
- `CLAUDE.md` says "test suite not implemented." Tests exist; update the line.
- The Compose restart-policy TODO and the `HF_TOKEN` TODO in `.env.example`.

---

## 3. Guardrails (non-negotiable)

**Public repo, synthetic data**
- Only data produced by the seed-42 generator, or derived from it (labels, splits, fine-tune sets). Never add real transcripts, real member data, or anything pasted from a client system.
- No client or employer names anywhere in code, data, comments, commit messages, or posts. `make check-public` (T00) greps for a public deny-list (`lyzr`, `gitagent`, `open-gitagent`) **plus** the patterns in `.forbidden_strings.local`, a gitignored file Stephen keeps on his machine. Never commit that file and never echo its contents into a PR.
- No secrets in git. Keys live in `.env` (gitignored); every new variable gets a placeholder in `.env.example`. gitleaks runs in CI.

**Don't break what's published**
- Posts 01–04 are live. Every command they show must keep working: `docker compose up -d`, `uv sync --extra dev`, `python data/synthetic/generate_data.py`, `python -m rag.pipeline --ingest`, `python -m rag.pipeline --query "..."`, `python -m ccai_mcp.server`, `pytest`. T00 adds a smoke test for these. If a change would break one, keep a compatible path or stop and ask. **Never edit a published post's text** to match changed code.
- MCP tool names and input schemas don't change. `test_server_handshake.py` guards the names.

**Money and accounts**
- Agents never run `terraform apply`, start a Fireworks fine-tuning job, rent a GPU, create a Databricks cluster, resume a Snowflake warehouse, or create any cloud account. You write the scripts and configs; Stephen runs anything that bills. Mark such steps in the PR as **"Stephen runs:"** with the exact command.
- Everything must pass `make test` offline: no network, no keys, no DB beyond Compose. Tests that need keys or cloud are marked `@pytest.mark.integration` and skipped by default.

**Honest numbers**
- Every results table comes from a run of code in this repo, written to `results/` (contract in §4.3) with the date, git SHA, hardware, and exact model and package versions. No projected, estimated, or "typical" figures. If a run didn't happen, the table says "not run."
- README and docs describe what's built today. Use "planned" for the rest.

**Engineering defaults**
- Python 3.12, `uv`, exact pins in `uv.lock`. New heavy dependencies go in a dependency group (`lance`, `agent`, `finetune`, `snowflake`, `databricks`), not the base install.
- `ruff` clean. Type hints on new public functions. Small modules.
- Idempotent everywhere: ingest, seed, loaders, and evals can run twice with the same result. Identity is by key (`call_id`), never by position or search rank.

---

## 4. Contracts (so parallel tickets don't collide)

### 4.1 Retriever (owned by R1; everyone else consumes it)
```python
# retrieval/base.py
@dataclass(frozen=True)
class Hit:
    call_id: str
    text: str
    score: float           # higher = more relevant, normalized per backend; document how
    metadata: dict

class Retriever(Protocol):
    name: str                                    # "pgvector" | "lancedb"
    def ingest(self, docs: list[dict]) -> int: ...          # upsert by call_id; returns rows written
    def search(self, query: str, k: int = 5,
               where: dict | None = None,
               mode: Literal["vector", "fts", "hybrid"] = "vector") -> list[Hit]: ...
    def get_by_id(self, call_id: str) -> Hit | None: ...   # metadata filter, never semantic
    def count(self) -> int: ...

def get_retriever(backend: str | None = None) -> Retriever   # RETRIEVER_BACKEND env, default "pgvector"
```
- `where` uses a small portable subset: equality, `in`, and date ranges (`gte`, `lte`, `lt`) on `category`, `outcome`, `date`, `call_id`. A date-only bound means the whole day (stored `date` is a datetime); `validate_where` normalizes it once. Each backend translates it.
- A backend that can't do `fts` or `hybrid` raises `NotImplementedError` with a clear message.
- Both backends use the **same embedding function** from `rag/embeddings.get_embeddings()`, so the comparison is fair.

### 4.2 Model providers (owned by P1)
`LLM_PROVIDER` = `openai` (any OpenAI-compatible endpoint, default OpenRouter; unchanged) | `ollama` | `anthropic` | `fireworks` | `vllm`.
New variables: `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `FIREWORKS_API_KEY`, `FIREWORKS_MODEL`, `VLLM_BASE_URL`, `VLLM_MODEL`. Fireworks and vLLM go through the OpenAI-compatible client. One function, `get_llm(provider: str | None = None)`, so evals can loop over providers in a single process.

### 4.3 Results
`results/<area>/<YYYY-MM-DD>_<run-name>.json`, where area is `retrieval`, `finetune`, `serving`, `evals`, or `semantics`:
```json
{"date": "...", "git_sha": "...", "area": "...", "run": "...",
 "hardware": "e.g. M2 Pro 32GB | Azure NC A10 spot | Snowflake XS",
 "versions": {"python": "...", "lancedb": "...", "model": "..."},
 "params": {...}, "metrics": {...}, "notes": "..."}
```
`python -m tools.results render <area>` writes `results/<area>/README.md` tables from the JSON. Docs and posts quote the rendered tables, never retyped numbers.

### 4.4 Layout
```
retrieval/        base.py, pgvector_backend.py, lancedb_backend.py, bench.py, labels/
agent/            graph.py, state.py, nodes/, subgraphs/, prompts/, langgraph.json
models/finetune/  data_gen.py, sft_lora.py, fireworks/, serve/ (vLLM configs), loadtest/
evals/            datasets/, evaluators.py, run.py
olap/dbt/         (existing) + profiles for snowflake and databricks, loaders/
infra/azure/      modules: core, data, apps, compute-gpu, databricks, snowflake; envs/dev
tools/            results.py, check_public.sh
results/          JSON plus rendered READMEs
```
`rag/` and `ccai_mcp/` stay where they are. `rag/` becomes a thin layer over `retrieval/`.

### 4.5 Env var rule
Any PR that adds an env var also adds it to `.env.example` and to the variables block in `CLAUDE.md`.

---

## 5. Tickets

Tier key: **C** = cheap OpenRouter model via firstmate (mechanical, fully specified, verified by command); **S** = Sonnet 5.5 via second-mate (most implementation); **O** = Opus 5.5 (design, dataset authoring, wave-end review). Cheap-tier PRs always get an S review before merge.

### Wave 0: foundation (start now, Thu–Fri Oct 1–2)

**T00 · Repo hygiene and CI** · C (S review) · deps: none
- Add `ruff` config, a `Makefile` (`up down seed ingest test lint check-public demo bench evals`; stub targets print "not yet" until their ticket lands), `tools/check_public.sh` (public deny-list plus optional `.forbidden_strings.local`; add the latter to `.gitignore`), and a GitHub Actions CI workflow: ruff, `pytest -m "not integration"`, gitleaks, check-public.
- Add `tests/test_published_commands.py`: runs the generator and imports the entry points of the published commands without network.
- Fix the drift listed in §2.
- **Verify:** `make lint && make test && make check-public`; CI green on the PR.

**P1 · Provider registry** · C (S review) · deps: none
- Implement §4.2 in `rag/pipeline.py` (or `models/providers.py`, re-exported). The default behavior must stay byte-for-byte the same.
- **Verify:** unit tests that build each provider's client from env without calling it; `python -m rag.pipeline --query` still works with the existing `.env` (Stephen runs).

**R1 · Retriever interface + pgvector backend** · S · deps: none
- Implement §4.1 and port the pgvector path onto it. Make `ingest` idempotent (upsert by `call_id`). Point `rag/pipeline.py` and `ccai_mcp/tools.py` at `get_retriever()`. Tool behavior is unchanged.
- **Verify:** `make up && make seed && make ingest && make ingest`, then `count()` equals the number of transcripts (not double); the handshake test passes; `python -m rag.pipeline --query "fraud disputes from last week"` returns grounded output.

**R2 · LanceDB backend** · S · deps: R1 (interface only; can start from §4.1)
- LanceDB OSS, embedded: no server and no Compose service. `LANCE_URI` defaults to `data/lance` (gitignored); an `az://` or `s3://` URI must work unchanged for the cloud profile.
- Support `vector`, `fts` (native full-text index on `text`), and `hybrid` (with a documented reranker). Translate `where` to a LanceDB SQL filter with **prefiltering**. Implement `get_by_id` as a pure filter.
- Build a vector index only where it's meaningful (IVF-PQ needs enough rows; with ~1,250 documents, brute force may win). Record which you chose and why in the module docstring.
- Dependencies go in the `lance` group. Check the current LanceDB docs for API names before writing code; the API moves.
- **Verify:** `RETRIEVER_BACKEND=lancedb make ingest` twice gives a stable count; `pytest tests/retrieval -m "not integration"` covers vector, fts, hybrid, where, and get_by_id against a 50-document fixture.

**R3 · Retrieval benchmark** · S, labels by O · deps: R1, R2
- **Labels (O):** about 40 queries in `retrieval/labels/queries.jsonl`, each with relevant `call_id`s **derived from generator metadata** (category, outcome, product, date), never from either retriever's output. Mix types: topical, filter-heavy ("escalated fraud calls in March"), exact-phrase (where FTS should win), and paraphrase (where vectors should win).
- **Bench (S):** `make bench` runs every query through both backends and every mode, and records recall@5, recall@10, MRR, p50/p95 query latency, ingest time, and index build time to `results/retrieval/`. Optional `--scale N` replicates the corpus with new ids to show index behavior at 100K+ rows, labeled as synthetic scale-up.
- **Stretch (S, only if R1–R3 are green by Mon Oct 5):** query the same Lance dataset from DuckDB through the Lance extension, if it exists in the current DuckDB release. Check before building; drop it if it doesn't.
- **Hard deadline: merged with a rendered results table by Tue Oct 6, 6pm CT.**
- **Verify:** `make bench` prints the table; the JSON validates against §4.3.

**D0 · Design specs** · O · deps: none (run in parallel with the above)
Three short design notes in `docs/design/`, each under two pages, each ending with the ticket list it implies:
1. `finetune.md`: the fine-tune-or-retrieve experiment (W1). Task schema, splits, base model choice for a single 24GB GPU, LoRA hyperparameters with reasons, the four comparison arms, eval metrics, and what would count as a fair comparison.
2. `agent-graph.md`: the LangGraph design (W2). State schema, nodes, edges, the interrupt contract, subgraphs, the checkpointer, and how tools come in over MCP.
3. `infra.md`: Terraform module boundaries, provider versions (azurerm 4.x), state backend, regions (East US 2 or Central US for Azure; Snowflake stays on its AWS region), and the cost guardrails.

### Wave 1: fine-tune or retrieve (Sat–Sun Oct 3–4)
Follows `docs/design/finetune.md`. Task: transcript → structured JSON summary (`reason_for_call`, `product_line`, `metric_mentioned`, `resolution`, `follow_up`).

**F1 · Dataset** · S · deps: D0.1 · **due Fri night**
- `models/finetune/data_gen.py`: gold labels for about 1,000 transcripts from a frontier model (provider via §4.2), with JSON-schema validation, retries, and resume. Each record is keyed by `call_id`, so a rerun skips finished records and never duplicates. 800/100/100 split by `call_id`, stratified by category. Writes `data/finetune/` (gitignored) and a manifest with counts and a hash.
- Writes `review_sample.jsonl`, 50 records, for **Stephen's hand check**.
- **Stephen runs:** the label generation (API spend).

**F2 · LoRA SFT (open source)** · S · deps: F1
- TRL or Unsloth, LoRA on a 3B–8B open model per D0.1. Chat template, loss on the assistant tokens only, eval loss on the dev split, adapter saved with a model card.
- Runs on a single 24GB GPU (an Azure spot VM if quota is granted, otherwise an hourly rental). Include a `--dry-run` that runs 10 steps on CPU with a tiny model so CI can exercise it.
- **Stephen runs:** the real training job.

**F3 · Fireworks fine-tune** · S · deps: F1
- Convert the same split to Fireworks' dataset format; scripts to upload, create the job, poll, and deploy, using the current Fireworks docs and CLI. Log every friction point in `models/finetune/fireworks/NOTES.md` as it happens.
- **Stephen runs:** the job.

**F4 · vLLM serving** · S · deps: F2 (adapter), D0.3
- `models/finetune/serve/`: vLLM configs for (a) the base model plus the LoRA adapter via multi-LoRA and (b) a quantized variant (AWQ or FP8). Plus a cloud-init script for the Azure GPU VM (driver, vLLM, systemd unit, idle auto-shutdown). The endpoint is consumed via `LLM_PROVIDER=vllm`.

**F5 · Load test** · C (S review) · deps: F4
- A wrapper over vLLM's serving benchmark at two traffic shapes (steady and bursty). Captures TTFT, inter-token latency, throughput, and cost per million tokens from an hourly GPU price passed as a flag. Writes to `results/serving/`.

**F6 · Evals across arms** · design O, build S · deps: F1, F2–F4 as available
- Arms: base model, fine-tuned model (vLLM), Fireworks fine-tune, frontier API with prompting, and RAG plus prompting. Metrics: field-level exact match per JSON key, JSON validity rate, LLM-as-judge for summary quality (judge prompt versioned, judge model ≠ any arm), latency, and cost. Writes to `results/finetune/`.
- An arm that didn't run is reported as "not run," not dropped silently.

### Wave 2: agent and observability (week of Oct 5)
Follows `docs/design/agent-graph.md`.

**L1 · LangGraph agent** · S · deps: D0.2, R1
- `agent/`: `classify → (retrieve | resolve_metric | summarize_call) → ground → answer`, with a `clarify` node. When the question uses an ambiguous term (start with "rate," "balance," "LCV"; the list is data, not code), the graph **interrupts** and asks instead of guessing. `ask_the_analyst` is a subgraph. Postgres or SQLite checkpointer, so an interrupted run resumes by thread id.
- Tools come from the existing MCP server through `langchain.mcp`, so Claude Desktop and the graph share one tool implementation. No duplicate tool code in `agent/`.
- **Verify:** unit tests with a fake LLM covering each route and the interrupt/resume path; `langgraph dev` starts.

**L2 · LangSmith tracing and evals** · dataset O, build S · deps: L1
- Tracing on by env (`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`), off by default in tests.
- **Golden set (O):** about 50 questions in `evals/datasets/agent_golden.jsonl`: ambiguous terms (expect an interrupt), metric questions (expect the right metric name and attached SQL), call lookups (expect the right `call_id`), and open questions (expect citations).
- **Evaluators:** route correctness, interrupt fired when it should and not otherwise, metric chosen, SQL present, citation present and real, and LLM-as-judge for the answer. `make evals` runs offline against a fake LLM for the deterministic checks; `make evals-live` uploads to LangSmith (**Stephen runs**).
- CI runs the deterministic subset as a regression gate.

**L3 · Compose and demo** · C (S review) · deps: L1, R1
- Compose services: `seed` and `dbt` (one-shot), `mcp-server`, `langgraph-api`, `evals` (one-shot); profile `ui` (open-webui, structurizr); profile `gpu` (vllm). Healthchecks and `depends_on: condition` throughout; resolve the restart-policy TODO. One app `Dockerfile`.
- `make demo`: brings the stack up and runs five scripted questions (one per route, plus one interrupt) with readable output.
- **Verify:** on a clean clone, `cp .env.example .env && make demo` works with `LLM_PROVIDER=ollama` and no cloud keys.

### Wave 3: one metric, three warehouses (week of Oct 12; Snowflake must finish by Fri Oct 23)

**S1 · Portable dbt** · S · deps: none
- Audit the models for Postgres-only SQL (date spines, `generate_series`, casts, string functions) and replace it with dbt cross-database macros. Add `snowflake` and `databricks` targets to `profiles.yml`, every value from env. Snowflake uses **key-pair auth** for the dbt user. Add matching adapter versions for `dbt-core 1.12.x` in the `snowflake` and `databricks` groups.
- **Verify:** `dbt build --target dev` (Postgres) is unchanged and green; `dbt parse` passes for all three targets offline.

**S2 · Loaders** · S · deps: S1
- `olap/dbt/loaders/`: load the same OLTP JSON into Snowflake (staged file plus `COPY INTO`, or `write_pandas`) and Databricks (a Unity Catalog volume plus `COPY INTO`, or the SQL connector). Idempotent: truncate-and-load or merge by key.
- **Stephen runs:** both loads.

**S3 · Parity check** · C (S review) · deps: S2
- `make parity`: runs `mf query` for five agreed metrics (chosen from `metrics.yml`; list them in the PR) on all three targets and diffs the numbers to a tolerance. Writes to `results/semantics/` with each target's generated SQL saved next to the numbers, so the dialect differences are visible.

**S4 · Apache Ossie export** · research O, build S · deps: S1
- Find the current Apache Ossie spec and whether a dbt/MetricFlow converter exists. If one exists, use it and commit the output. If not, write `docs/semantics/ossie_mapping.md`: a field-by-field mapping from our `semantic_models.yml`/`metrics.yml` to the spec, citing the spec version. **Don't invent spec fields.** Link to sources.

**I-SF · Snowflake objects in Terraform** · S · deps: D0.3
- `infra/azure/modules/snowflake` using the Snowflake provider: an XS warehouse with 60-second auto-suspend, a database, a role, and the dbt user with key-pair auth. Plan only.

### Rolling: Azure infrastructure
Follows `docs/design/infra.md`.

**I1 · Terraform modules** · S, one PR per module · deps: D0.3
- `core` (resource group, Key Vault, Log Analytics, **budget with alerts at 50% and 90%**), `data` (Postgres Flexible Server with the pgvector extension allow-listed; ADLS Gen2 for the Lance datasets and dbt artifacts), `apps` (ACR; Container Apps for `mcp-server` and `langgraph-api` with **Easy Auth via Entra ID**), `compute-gpu` (spot VM, nightly auto-shutdown, F4's cloud-init), `databricks` (workspace plus one small job cluster with auto-terminate).
- `infra/azure/envs/dev` composes them. Once `apps` lands, mark `infra/terraform/` deprecated in its README; delete it only after Stephen confirms.
- **Verify:** `terraform fmt -check`, `terraform validate`, and `tflint` in CI; `terraform plan` with a dummy tfvars runs offline where the providers allow. **Stephen runs:** `apply` and `destroy`.

### Wave 4: writing (after each piece has results)

**B1–B4 · Post drafts** · O drafts, Stephen finalizes · deps: the results they cite
- Content briefs for all four (thesis, evidence paths, must-not-claim lines) are in `blogs/PROPOSED_POSTS.md`. Draft from the brief, not from this list.
- B1 "Fine-tune or retrieve? Measuring it on call summaries." Blocked until F2–F6 have results; F1 (the labeled dataset) is done.
- B2 "One metric, three warehouses." Ready except the headline claim: the models build on all three targets, and the Ossie export and its known issues are written up, but "the numbers match" needs S3 (`make parity`). Without S3 the post says "builds on," never "matches."
- B3 "Reading an agent graph as a statechart." Ready (L1 and L2). The graph has hierarchy (the `analyst` subgraph) and a persisted interrupt; it has no orthogonal regions or history states, and the post says so.
- B4 "pgvector and LanceDB on 100,000 calls." Ready (R1–R3 results). New since this plan was written.
- The evals findings (three environment causes behind the first live run) go into post 10, "Trust, but Verify," rather than a new post.
- Before drafting, propose in a PR comment whether each one fills an existing placeholder slot (05–12) or becomes a new post. Stephen decides.
- Every number in a post comes from a rendered `results/` table. Every command shown is one the draft's author ran.
- Plain, specific, first-person engineering prose. Avoid aphorism openers, "here's what everyone gets wrong," setup-then-hero sentences, "not X, but Y" reveals, marketing adjectives, and titles that start with "What." Stephen rewrites in his own voice; the draft's job is structure and accurate facts.

---

## 6. Dependency and dispatch summary

| Ticket | Tier | Depends on | Due |
|---|---|---|---|
| T00 hygiene/CI | C | none | Fri Oct 2 |
| P1 providers | C | none | Fri Oct 2 |
| R1 Retriever + pgvector | S | none | Sat Oct 3 |
| R2 LanceDB | S | R1 (interface) | Sun Oct 4 |
| R3 bench | O labels, S build | R1, R2 | **Tue Oct 6, 6pm CT** |
| D0 design specs | O | none | Fri Oct 2 |
| F1 dataset | S | D0.1 | Fri Oct 2, night |
| F2 LoRA SFT | S | F1 | Sat Oct 3 |
| F3 Fireworks FT | S | F1 | Sat Oct 3 |
| F4 vLLM serve | S | F2, D0.3 | Sun Oct 4 |
| F5 load test | C | F4 | Sun Oct 4 |
| F6 evals | O design, S build | F1–F4 | Sun Oct 4 |
| L1 agent | S | D0.2, R1 | Fri Oct 9 |
| L2 LangSmith | O dataset, S build | L1 | Fri Oct 9 |
| L3 Compose/demo | C | L1, R1 | Fri Oct 9 |
| S1–S4, I-SF | S / C / O | as listed | Snowflake by Fri Oct 23 |
| I1 modules | S | D0.3 | rolling |
| B1–B4 | O | results | after each piece |

**Parallel lanes that don't touch the same files:** {T00, P1} · {R1 → R2 → R3} · {D0 → F1 → F2/F3 → F4 → F5/F6} · {I1}. Merge order inside a lane follows the arrows. The only shared files are `pyproject.toml`/`uv.lock`, `.env.example`, `CLAUDE.md`, and the `Makefile`: rebase onto `main` before opening the PR and resolve conflicts by keeping both sides.

**Routing rule of thumb:** if the ticket can be verified entirely by running a command, and the spec leaves no design choices, it's C. If it needs reading current library docs or making local design choices, it's S. If it sets a contract other tickets depend on, authors a dataset that defines "correct," or reviews a whole wave, it's O.

---

## 7. Review protocol

**The builder's PR description must contain:**
1. What changed, in three to six lines.
2. The verify commands, run by the builder, with output pasted (trimmed but real).
3. Tests added and what each one guards.
4. Env vars added.
5. "Stephen runs:" steps with exact commands, if any.
6. "Not done / known limits," and follow-ups.
7. **"Caught":** anything the builder got wrong and corrected along the way (a wrong API, a failing assumption). One line each.

**The reviewer** is a different agent from the builder, and on a different model when the builder was C:
- Checks out the branch in a fresh worktree and **runs the verify commands itself**. It doesn't trust the pasted output.
- Runs `make lint test check-public`.
- Reads the diff for scope creep, broken contracts (§4), published-command breakage, non-idempotent writes, and secrets.
- Checks that every claim in a README or doc change matches what the code does and what `results/` holds.
- Verdict: **approve**, **changes** (a numbered list), or **block** (a guardrail violation). Stephen merges.

**Wave-end review (O):** at the end of each wave, an Opus reviewer reads the merged wave cold, with only this file, `CLAUDE.md`, and the diff since the wave started. It runs `make demo` (plus `make bench` or `make evals` where relevant) on a clean clone and reports what a skeptical outside engineer would hit first. Findings become tickets.

**Corrections:** every "Caught" line and every reviewer finding that changed the code goes to Stephen. He logs them in his own corrections log (outside this repo). They're material for the agent-harness post (blog 12).

---

## 8. Stephen's checkpoints (agents wait on these; don't work around them)

| When | Stephen does |
|---|---|
| Now | Request Azure GPU quota (NC T4 or A10 family, spot, one region). If it isn't granted by Sat morning, F2 and F4 run on an hourly rental. |
| Now | Put keys in local `.env`: OpenRouter, Anthropic, Fireworks, LangSmith. Create `.forbidden_strings.local`. |
| Fri night | Run F1 label generation; hand-check the 50-record sample. |
| Sat–Sun | Start the F2 training and F3 Fireworks jobs; start and stop the GPU VM; run F5. |
| Mon–Tue | Run `make bench` on his machine for the canonical retrieval numbers. |
| Week of Oct 12 | Create the Snowflake dbt user and key pair, and the Databricks workspace or Free Edition; run the S2 loads and S3 parity. |
| Any `apply` | `terraform apply`, then `terraform destroy` at the end of the session. Check the budget alert email is set. |
| Every PR | Merge, or send back. Decide B1–B4 slots. Publish posts. |

---

## 9. Dispatch brief template

Paste this into firstmate or second-mate, one brief per ticket:

```
Repo: HendoCode/contact-center-ai   Branch: t/<id>-<slug> (new worktree from main)
Ticket: <id> · <title>   Tier: <C|S|O>   Due: <date>
Read first: CLAUDE.md, docs/AGENT_HANDOFF.md §0, §3, §4, and §5/<id>. Then: <specific files>.
Goal: <one sentence from the ticket>
Acceptance: <the ticket's bullets, copied>
Verify (run these yourself and paste the output in the PR): <commands>
Out of scope: anything not listed above; list ideas under Follow-ups.
Stop and ask if: a decision isn't covered by the hand-off doc, a guardrail would be crossed,
  a step would cost money, or verify fails 3 times.
Deliver: a PR to main with the §7 description sections filled in.
```
