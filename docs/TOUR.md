# Tour: what was built, how to run it, where the code is

A walk-through of everything that landed in PRs #8–#28 (2026-09-29 to 2026-10-02), with a code map.
Each step has one label:

| Label | Meaning |
|---|---|
| **RUN NOW** | Offline. No Docker, no keys, no database. |
| **RUN NOW · DOCKER** | Needs Docker Compose and the local Ollama models. No cloud keys, no bill. |
| **NEEDS A CREDENTIAL** | Your run: an account, key or warehouse. Steps that cost money say so. |
| **READ THE CODE** | Built and checked in CI (validate, tests), but there is nothing to execute locally. |

Contents: [What this is](#what-this-is) · [Setup](#setup-once) · [A. Run now](#a-run-now-offline) ·
[B. Docker](#b-run-now--docker) · [C. Credentials](#c-needs-a-credential-your-run) ·
[D. Read the code](#d-read-the-code) · [Azure portal tour](#azure-portal-tour) ·
[Code map](#code-map) · [Cheat sheet](#cheat-sheet) · [Known limits](#known-limits-and-not-built-yet)

---

## What this is

One contact-center analytics system for a fictional credit union, on seed-42 synthetic data
(1,250 calls, 943 CSAT responses, an OLTP ledger of 25,000 transactions). The same data feeds
four layers, and each layer has a swap point set by configuration:

```
data/synthetic/generate_data.py  (seed 42)
  ├─ transcripts.json ──► retrieval/        Retriever interface: pgvector ⇄ LanceDB   (RETRIEVER_BACKEND)
  ├─ OLTP JSON ─────────► olap/dbt/         dbt star schema + MetricFlow metrics      (--target dev | snowflake | databricks)
  │                        olap/dbt/loaders/  OLTP JSON → Snowflake / Databricks raw tables
  └─ (both) ────────────► ccai_mcp/server.py  5 MCP tools over stdio
                             └─► agent/     LangGraph graph, tools bound via langchain.mcp
                                   └─► evals/  golden set: offline gate (make evals) + LangSmith (make evals-live)
chat models: rag/pipeline.py get_llm()      (LLM_PROVIDER = openai | ollama | anthropic | fireworks | vllm)
infra: docker-compose.yml (local, no bill)  ·  infra/azure/ Terraform (Azure + a Snowflake root)
```

The five MCP tools: `search_transcripts`, `get_call_summary`, `query_csat` (data tools) and
`query_metric`, `ask_the_analyst` (semantic-layer tools that shell out to MetricFlow's `mf`).

---

## Setup (once)

```bash
cp .env.example .env
uv sync --extra dev --group agent --group lance --group dbt
```

The `snowflake` and `databricks` groups conflict with each other, so they are never in this
sync; the Make targets that need them pass `--group` themselves.

---

## A. Run now (offline)

Every command here was run for this doc on 2026-10-02 (Linux x86_64, Python 3.12, no Docker,
no keys). The output shown is trimmed real output.

**A1. Generate the data.** Deterministic, so every later count matches.

```bash
make seed            # = uv run python data/synthetic/generate_data.py
```
```
interactions: 1250, interaction_accounts: 1383
transcripts: 1250, csat: 943
transactions: 25000
```

**A2. The CI gates.**

```bash
make lint            # All checks passed!
make test            # 274 passed, 5 skipped, 9 deselected
make check-public    # check-public: clean
```

**A3. Semantic layer with no database.** `dbt parse` writes `target/semantic_manifest.json`
without connecting anywhere, and `mf` can list the catalog and compile SQL from it.

```bash
cd olap/dbt
uv run --group dbt dbt parse
uv run --group dbt mf list metrics          # "We've found 43 metrics."
uv run --group dbt mf query --explain \
  --metrics average_mortgage_note_rate,average_credit_card_purchase_apr,average_deposit_apy,average_heloc_current_rate,average_investment_return_pct
cd ../..
```

This is the ambiguity demo: "interest rate" is five declared metrics, and the compiled SQL shows
each one filtered to its own line of business:

```
WHERE product__lob = 'mortgage'
WHERE product__lob = 'cards'
WHERE product__lob = 'banking'
WHERE product__lob = 'home'
WHERE product__lob = 'investments'
```

Running the query for numbers needs Postgres (B2).

**A4. MCP tools through LangChain.** Starts `python -m ccai_mcp.server` as a stdio subprocess
and lists its tools through `langchain.mcp` (`MCPAdapter`), the same binding the agent uses.

```bash
uv run --group agent python -c "import asyncio; from agent.tools import MCPToolbox; print(sorted(asyncio.run(MCPToolbox().tools())))"
```
```
['ask_the_analyst', 'get_call_summary', 'query_csat', 'query_metric', 'search_transcripts']
```

`uv run python -m ccai_mcp.server` on its own starts the server and waits for a client on stdin
(Ctrl-C to stop); `tests/test_server_handshake.py` checks the handshake and the five names.
Calling a tool for real needs B (a database, and a chat model for the summarizing tools).

**A4b. MCP over streamable HTTP (opt in).** For clients on other machines: Claude Desktop (via
`mcp-remote`), Cursor, open-webui. Off unless asked; loopback-bound, and a non-loopback bind
refuses to start without `MCP_AUTH_TOKEN`.

```bash
MCP_AUTH_TOKEN=demo-token uv run python -m ccai_mcp.server --http &      # http://127.0.0.1:8000/mcp
MCP_AUTH_TOKEN=demo-token uv run python -m ccai_mcp.http_smoke           # lists the five tools
```

`ccai_mcp/README.md` has the URL shape, the safety rules, and the registration snippets. A tool
that reads Postgres needs B; the listing does not.

**A5. LangGraph dev server.** Starts without a model or database. Questions need a chat
model, so running one is B.

```bash
uv run --group agent langgraph dev --config agent/langgraph.json --no-browser
curl -s localhost:2024/ok            # {"ok":true}
```

Studio: `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`. API docs:
`http://127.0.0.1:2024/docs`. It prints an end-of-life warning for langgraph-api 0.13.6.

**A6. Agent evals offline (the LangSmith golden set, no LangSmith).** The real graph with a
scripted classifier and stub tools, so it scores the graph's deterministic behavior, not the model.

```bash
make evals
```
```
      group            n        route    interrupt       metric          sql    citations      call_id
        all           51         1.00         1.00         1.00         1.00         1.00         1.00
  ambiguous           11         1.00         1.00         1.00         1.00          n/a          n/a
     metric           18         1.00         1.00         1.00         1.00          n/a          n/a
call_lookup           10         1.00         1.00          n/a          n/a         1.00         1.00
       open           12         1.00         1.00          n/a          n/a         1.00          n/a

regression gate passed (0 known failures)
```

**A7. The two label files are derived, not hand-picked.** Each `--check` re-derives the file from
the generator output and exits 1 if the committed copy differs.

```bash
uv run python -m evals.datasets.build_golden --check     # ... agent_golden.jsonl is up to date
uv run python -m retrieval.labels.build_labels --check   # ... queries.jsonl is up to date
```

**A8. LanceDB backend on its offline fixture.** A 50-document fixture with a hashed
bag-of-words embedding (`tests/retrieval/conftest.py`): vector, FTS, hybrid, `where`
prefiltering, idempotent upsert.

```bash
uv run pytest tests/retrieval/test_lancedb_backend.py -q
```

**A9. DuckDB reads the Lance dataset.** Needs network once, to `INSTALL lance`.

```bash
uv run pytest tests/retrieval/test_duckdb_lance.py -m integration -q    # 8 passed
```

**A10. Warehouse loaders, dry run.** Prints every statement both loaders would send (302 lines),
connects to nothing.

```bash
make load-dry-run
```
```
-- snowflake: setup
CREATE SCHEMA IF NOT EXISTS MY_DATABASE.raw;
CREATE STAGE IF NOT EXISTS MY_DATABASE.raw.ccai_loader_stage;
-- household: 150 rows
CREATE TABLE IF NOT EXISTS MY_DATABASE.raw.household (...);
PUT 'file:///tmp/ccai-load-.../household.ndjson' @MY_DATABASE.raw.ccai_loader_stage/household/ ...;
TRUNCATE TABLE MY_DATABASE.raw.household;
COPY INTO MY_DATABASE.raw.household (...) FROM (SELECT $1:household_id::VARCHAR(36), ...) ... FORCE = TRUE ...;
SELECT count(*) FROM MY_DATABASE.raw.household;
```

**A11. Fine-tune dataset.** Gold labels and the 800/150/300 split, offline.

```bash
make finetune-data                       # writes data/finetune/ (gitignored); "n_calls": 1250
make finetune-label ARGS="--dry-run"     # 950 calls pending of 950; no API call made
```

---

## B. Run now · Docker

Not run for this doc (no Docker on the machine that wrote it). Commands checked against the
Makefile, `docker-compose.yml` and each module's argparse.

**B1. The whole stack in one command.**

```bash
make demo        # chat + embeddings on Ollama; first run pulls ~2 GB and embeds 1,250 calls on CPU
make down        # stops every profile
```

It pulls `llama3.2` and `nomic-embed-text`, applies the OLTP schema, seeds Postgres, embeds the
transcripts, runs `dbt build`, starts `langgraph dev` on `localhost:2024`, then runs five scripted
questions (`agent/demo.py`): one per route, one through `ask_the_analyst`, and "What is our average
rate?", which interrupts with the seven rate metrics and resumes with
`average_mortgage_note_rate`. `make demo DEMO_LLM_PROVIDER=openai` swaps chat to OpenRouter (needs
`OPENAI_API_KEY`, costs a little).

**B2. Step by step on the host** (for screen-sharing one layer at a time).

```bash
make up                                                   # db, ollama, Open WebUI :9090, Structurizr :8080
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull llama3.2
# in .env: LLM_PROVIDER=ollama  (EMBEDDING_PROVIDER=ollama is already set)

olap/oltp/apply.sh                                        # OLTP schema
uv run python olap/seed.py                                # OLTP JSON -> Postgres (idempotent)
make ingest                                               # transcripts -> pgvector (upsert by call_id)

cd olap/dbt
uv run --group dbt dbt build                              # star schema + data tests
uv run --group dbt mf query --metrics banking_available_balance,credit_card_outstanding,net_member_liquidity --decimals 2
cd ../..
```

`olap/dbt/README.md` records the values from a seeded run (for example
`average_mortgage_note_rate` 6.5888, `average_deposit_apy` 1.9373, `net_member_liquidity`
35,932,168.86). They were not re-run for this doc.

The two semantic-layer MCP tools, called directly:

```bash
uv run python -c "from ccai_mcp.metrics import query_metric; print(query_metric(['average_mortgage_note_rate', 'average_deposit_apy'], decimals=4))"
uv run python -c "from ccai_mcp.metrics import ask_the_analyst; print(ask_the_analyst('what is our average interest rate?', decimals=4))"
```

`query_metric` returns the table plus the SQL MetricFlow generated. `ask_the_analyst` asks the
chat model to map the question to declared metric names, then runs them through `query_metric`;
it never returns one blended number.

**B3. The retrieval swap.** Same interface, same embedding function, one env var. LanceDB is
embedded (no server); with Ollama running it needs no Postgres.

```bash
RETRIEVER_BACKEND=lancedb uv run --group lance python -m rag.pipeline --ingest   # writes data/lance/
RETRIEVER_BACKEND=lancedb uv run --group lance python -m rag.pipeline --ingest   # again: "1250 documents upserted (1250 total, lancedb)"
RETRIEVER_BACKEND=lancedb uv run --group lance python -c "
from retrieval import get_retriever
r = get_retriever()
for mode in ('vector', 'fts', 'hybrid'):
    hits = r.search('unauthorized charge dispute', k=3, mode=mode, where={'outcome': 'escalated'})
    print(mode, [(h.call_id, round(h.score, 3), h.metadata['category']) for h in hits])"
```

Swap `RETRIEVER_BACKEND=pgvector` (the default, after `make ingest`) and `mode='vector'` gives the
same shape of answer; `mode='fts'` raises `NotImplementedError`, because the pgvector backend is
vector-only. For this doc the LanceDB half was run on the real 1,250-call corpus with the
fixture's offline embedding in place of Ollama: ingest twice gave 1,250 rows both times, and all
three modes returned escalated calls only. The scores from that run are not meaningful.

**B4. The R3 benchmark.** Writes `results/retrieval/<date>_<run>.json` and re-renders
`results/retrieval/README.md`. Those are real results, so commit them if you want them public.

```bash
make bench ARGS="--backends lancedb"     # Ollama only, no Postgres
make bench                               # both backends, all modes
make bench ARGS="--scale 80"             # 100,000 rows: the size where LanceDB builds IVF-PQ
```

**B5. RAG answer and agent outside Compose.** Both need a chat model (`LLM_PROVIDER=ollama`) and
the B2 stack; the agent also needs `dbt build` done.

```bash
uv run python -m rag.pipeline --query "fraud disputes from last week"
uv run --group agent python -m agent.demo           # the five questions, Postgres checkpointer
```

A sample supervisor chat built from the real output of all five tools, SQL attached: `docs/demo-chat.md`
(regenerate after B2 with `uv run --group agent --group dbt python -m tools.demo_chat`).

---

## C. Needs a credential (your run)

**C1. LangSmith tracing.** Needs a LangSmith API key. Set in `.env`:

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your key>
LANGSMITH_PROJECT=contact-center-ai
```

Then every graph run from `make demo`, `langgraph dev` or `make evals-live` is traced.
`make test` and `make evals` force tracing off whatever `.env` says.

**C2. Live evals in LangSmith. Costs API money.** Needs the B2 stack, the agent's provider key,
`LANGSMITH_API_KEY`, and a judge model unlike the agent's (`EVAL_JUDGE_PROVIDER`,
`EVAL_JUDGE_MODEL`, default `claude-opus-5-5`, so `ANTHROPIC_API_KEY` too). It refuses to start
when the two models match.

```bash
make evals-live ARGS="--limit 5"    # cheap smoke run
make evals-live                     # all 51; writes results/evals/ and a LangSmith experiment
```

**C3. Snowflake. Costs warehouse credits** (XS, 60 s auto-suspend if you used the Terraform root).
The database, warehouse and role must exist; the loader creates the raw schema and the stage.
Key-pair auth only. Neither the loader nor `dbt build --target snowflake` has been run against a
real account yet.

Fill in the Snowflake block of `.env` (the variable list is in `.env.example`). If you applied the
Terraform root, `terraform output dbt_env` in `infra/azure/envs/snowflake` prints the account, user,
role, warehouse, database and schema:

```bash
SNOWFLAKE_ACCOUNT=                    # <org>-<account>
SNOWFLAKE_USER=                       # the dbt service user
SNOWFLAKE_PRIVATE_KEY_PATH=~/.snowflake/ccai_dbt_key.p8
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=     # only for an encrypted key
SNOWFLAKE_ROLE=
SNOWFLAKE_WAREHOUSE=
SNOWFLAKE_DATABASE=
SNOWFLAKE_SCHEMA=marts
SNOWFLAKE_RAW_SCHEMA=raw
```

Then export it (make and dbt read the shell, not `.env`) and run:

```bash
set -a; . ./.env; set +a
make load-snowflake          # uv run --group dbt --group snowflake python -m olap.dbt.loaders snowflake
cd olap/dbt
uv run --group dbt --group snowflake dbt build --target snowflake
```

The dbt user, warehouse, role, database and a 5-credit resource monitor are in Terraform
(`infra/azure/envs/snowflake/README.md` has the key-pair generation and the plan/apply steps).

**C4. Databricks. Costs DBUs** (and the Azure workspace, if you apply it). The catalog and a SQL
warehouse or cluster HTTP path must exist; the loader creates the raw schema and the volume.

Fill in the Databricks block of `.env`:

```bash
DATABRICKS_HOST=                      # workspace URL
DATABRICKS_HTTP_PATH=                 # SQL warehouse or cluster HTTP path
DATABRICKS_TOKEN=                     # personal access token
DATABRICKS_CATALOG=
DATABRICKS_SCHEMA=marts
DATABRICKS_RAW_SCHEMA=raw
```

Then:

```bash
set -a; . ./.env; set +a
make load-databricks         # uv run --group dbt --group databricks python -m olap.dbt.loaders databricks
cd olap/dbt
uv run --group dbt --group databricks dbt build --target databricks
```

With `envs/dev` applied and `enable_databricks = true`, `terraform output databricks_workspace_url`
and `terraform output databricks_http_path` give the host and path.

**C5. Teacher labels for the fine-tune set. Costs API money.**

```bash
LLM_PROVIDER=anthropic ANTHROPIC_MODEL=claude-sonnet-5-5 make finetune-label ARGS="--limit 5"
```

**C6. Azure Terraform. `apply` costs money; `destroy` `envs/dev` each session.** Steps in
`infra/azure/README.md` (`bootstrap` once, then `envs/dev` init/plan/apply/destroy). `plan` needs
`az login`.

---

## D. Read the code

- **Terraform** (`infra/azure/`): `bootstrap` (state storage, Key Vault, subscription budget),
  `envs/dev` composing `modules/{core,data,apps,compute-gpu,databricks}`, and `envs/snowflake`
  composing `modules/snowflake`. Costly pieces are off by default (`enable_gpu`,
  `enable_databricks`, `enable_mcp_server`, `enable_agent_api`). CI runs `terraform fmt -check`,
  `validate` with `-backend=false`, and `tflint`; `validate` passes on all three roots today.
  `infra/terraform/` is the deprecated single-file version.
- **Fine-tune**: only F1, the dataset, is built (`models/finetune/`). LoRA SFT, the Fireworks
  fine-tune, vLLM serving, the load test and the fine-tune-vs-retrieve evals (F2–F6) are designed
  in `docs/design/finetune.md` and **not built yet**. The `vllm` Compose service (`gpu` profile)
  is declared only.
- **Apache Ossie export** (`docs/semantics/`): the semantic layer converted to the vendor-neutral
  Ossie spec 0.2.0.dev0, with a field-by-field map of what carries over and three known conversion
  issues. `tests/test_ossie_export.py` keeps it in step with the YAML.
- **Design notes**: `docs/design/agent-graph.md`, `docs/design/infra.md`, `docs/design/finetune.md`.

---

## Azure portal tour

What you can click into once `bootstrap` and `envs/dev` are applied (steps in
[`infra/azure/README.md`](../infra/azure/README.md)). Names follow the Terraform naming patterns;
`<suffix>` is a random string generated per root, so the real names are not stored in the repo.
Each row says how to find the real name: a `terraform output` run from that root, an `az` command,
or a portal search. You can also filter the portal on the `project = contact-center-ai` tag or the
`ccai` prefix. Set the groups once for the commands below (the subscription is whichever `az login`
selected):

```bash
az account show -o table                                # the subscription `az login` selected
terraform -chdir=infra/azure/envs/dev output          # every dev output at once
terraform -chdir=infra/azure/bootstrap output         # every bootstrap output at once
```

**Dev resource group `rg-ccai-dev`** (deployed in Central US; `envs/dev` defaults `location` to
`eastus2` per `docs/design/infra.md`, and `gpu_location` exists for the Central US Spot-quota
fallback, so the region is a tfvars override). Destroyed each session, so its suffixes can change
after a re-apply: look the names up again. Portal: Resource groups > `rg-ccai-dev`
(`terraform -chdir=infra/azure/envs/dev output -raw resource_group_name`).

| Resource | Name pattern | How to find the name | What to look at |
|---|---|---|---|
| PostgreSQL Flexible Server | `psql-ccai-dev-<suffix>` | the first label of `terraform -chdir=infra/azure/envs/dev output -raw postgres_fqdn`; or `az postgres flexible-server list -g rg-ccai-dev -o table` | B1ms, PostgreSQL 16, `VECTOR` allow-listed in `azure.extensions`, database `contactcenter` |
| Storage account (ADLS Gen2) | `stccaidev<suffix>` | `terraform -chdir=infra/azure/envs/dev output -raw adls_account_name` | filesystems `lance` (Lance datasets) and `dbt-artifacts` |
| Container registry | `acrccaidev<suffix>` | `terraform -chdir=infra/azure/envs/dev output -raw acr_name` | Basic SKU |
| Container Apps environment | `cae-ccai-dev` | fixed name: `az containerapp env show -g rg-ccai-dev -n cae-ccai-dev` | `mcp-server` and `agent-api` apps appear only when their `enable_*` flags are true (URLs: `terraform -chdir=infra/azure/envs/dev output app_urls`) |
| Log Analytics workspace | `log-ccai-dev` | fixed name: `az monitor log-analytics workspace show -g rg-ccai-dev -n log-ccai-dev` | 0.5 GB/day cap |
| User-assigned managed identity | `id-ccai-dev-app` | fixed name: `az identity show -g rg-ccai-dev -n id-ccai-dev-app` | the apps' identity: Key Vault secrets, ACR pull, storage |

**Bootstrap resource group `rg-ccai-bootstrap`** (East US 2). Persistent; never destroyed. Portal:
Resource groups > `rg-ccai-bootstrap`
(`terraform -chdir=infra/azure/bootstrap output -raw state_resource_group_name`).

| Resource | Name pattern | How to find the name | What to look at |
|---|---|---|---|
| Storage account | `stccaitf<suffix>` | `terraform -chdir=infra/azure/bootstrap output -raw state_storage_account_name` | Terraform state, versioning on |
| Key Vault | `kv-ccai-<suffix>` | `terraform -chdir=infra/azure/bootstrap output -raw key_vault_name` | app secrets (names only: never paste values into docs or PRs) |
| Budget (subscription scope) | `budget-ccai-monthly` | fixed name; portal: Cost Management > Budgets, or `az consumption budget show --budget-name budget-ccai-monthly` | alerts at 50% and 90% actual, 100% forecast |

**Entra ID:** the `id-ccai-dev-app` managed identity (portal: Managed Identities, or the `az identity
show` command above), and the service principal used for Terraform (portal: Entra ID > App
registrations > Owned applications; it is the identity you ran `az login --service-principal` with,
and its name is not a Terraform output).

**Cross-cloud by design.** Postgres, storage, the apps, the GPU VM and Databricks are on Azure.
Snowflake is not an Azure resource: it is a Snowflake trial account hosted on AWS, and the I-SF
root (`infra/azure/envs/snowflake`) provisions its objects (warehouse, database, role, dbt user,
resource monitor), plan-only until you apply it. It sits under `infra/azure/` and keeps its state in the same
Azure backend, under its own state key. Cross-cloud traffic is the few MB of loader files and dbt queries.

---

## Code map

| Topic | Files | What to point at |
|---|---|---|
| Retrieval interface | `retrieval/base.py` | `Hit`, the `Retriever` protocol (`ingest`/`search`/`get_by_id`/`count`), `validate_where`, `get_retriever()` reading `RETRIEVER_BACKEND` |
| LanceDB | `retrieval/lancedb_backend.py` | module docstring (index policy, score normalization); `merge_insert("call_id")` upsert; `_ensure_indexes` (FTS + BTree always, IVF-PQ at ≥100,000 rows); `search` with prefiltered `where` and RRF hybrid |
| pgvector | `retrieval/pgvector_backend.py` | LangChain `PGVector`, row id = `call_id`; vector-only, `fts`/`hybrid` raise |
| Retrieval benchmark | `retrieval/bench.py`, `retrieval/labels/` | method in the docstring; labels derived from generator facts (`build_labels.py`), capped recall@k, MRR@10, `--scale` |
| DuckDB over Lance | `retrieval/duckdb_lance.py`, `retrieval/README.md` | read-only SQL over the same dataset; gaps vs the LanceDB backend |
| Semantic layer | `olap/dbt/models/marts/semantic/metrics.yml`, `semantic_models.yml` | 43 metrics, none named `interest_rate`/`balance`/`lcv`; `filter: "{{ Dimension('product__lob') }} = '…'"`; derived `net_member_liquidity` and `member_lifetime_value` |
| Star schema | `olap/dbt/models/marts/dim/`, `fact/`, `olap/dbt/tests/` | `d_*`/`f_*` models; sign-convention and LOB-isolation tests |
| Warehouse targets | `olap/dbt/profiles.yml` | `dev` (Postgres), `snowflake` (key pair, no password), `databricks`; every value from env |
| Warehouse loaders | `olap/dbt/loaders/` | `__main__.py` (CLI), `snowflake_loader.py` (PUT + `COPY INTO`), `databricks_loader.py` (volume + `COPY INTO`), `schema.py` (DDL type mapping), `plan.py` |
| Portability guards | `tests/test_dbt_portability.py`, `tests/test_warehouse_loaders.py` | no Postgres-only SQL; loader plans checked against a fake |
| MCP server | `ccai_mcp/server.py` | the five `Tool(...)` declarations and `call_tool` dispatch (mcp 2.x low-level server); stdio by default |
| MCP over HTTP | `ccai_mcp/http_transport.py`, `ccai_mcp/http_smoke.py`, `ccai_mcp/README.md` | settings and the non-loopback refusal, constant-time bearer check, `/healthz`; smoke client; client snippets |
| Metric tools | `ccai_mcp/metrics.py` | `run_metricflow` (shells out to `mf`), `query_metric`, `resolve_metrics` + `ask_the_analyst` |
| LangGraph graph | `agent/graph.py`, `agent/state.py`, `agent/nodes/` | `build_graph`: classify → retrieve / summarize_call / resolve_metric → ground → answer |
| Interrupt | `agent/subgraphs/analyst.py`, `agent/data/ambiguous_terms.yml` | `clarify` is the only `interrupt()`; terms and candidates are data |
| Checkpointer | `agent/checkpoint.py` | `open_checkpointer()`: `AsyncPostgresSaver` in a `langgraph` schema |
| LangChain MCP binding | `agent/tools.py` | `from langchain.mcp import MCPAdapter`, stdio to `python -m ccai_mcp.server` |
| Serving | `agent/langgraph.json`, `agent/demo.py` | `langgraph dev` entry point; the five scripted questions |
| LangSmith tracing | `agent/tracing.py` | on by env only; forced off in tests and offline evals |
| Evals | `evals/run.py`, `evals/evaluators.py`, `evals/offline.py`, `evals/datasets/` | offline gate vs `--live` LangSmith experiment; evaluators share LangSmith's signature; golden set derived by `build_golden.py`; `known_failures.json` |
| Model routing | `rag/pipeline.py` | `get_llm(provider=None)`: openai-compatible, ollama, anthropic, fireworks, vllm. There is no `models/providers.py`; the registry lives here |
| Embeddings | `rag/embeddings.py` | `get_embeddings()` (`EMBEDDING_PROVIDER`), shared by both backends |
| Fine-tune data | `models/finetune/` | `gold.py`, `splits.py`, `data_gen.py` (`build`/`label`/`finalize`) |
| Infra | `infra/azure/` | `bootstrap/`, `envs/dev/`, `envs/snowflake/`, `modules/*` |
| Local stack | `docker-compose.yml`, `Dockerfile`, `Makefile` | profiles: default, `app` (`make demo`), `gpu` |
| Results contract | `tools/results.py`, `results/` | JSON per run, rendered READMEs (both say "not run" today) |

---

## Cheat sheet

### LanceDB

- **Where:** `retrieval/lancedb_backend.py` behind the same `Retriever` protocol as pgvector;
  `RETRIEVER_BACKEND=lancedb` swaps it everywhere (ingest, MCP tools, the agent's `ground` node).
- **Embedded:** no server and no Compose service. `LANCE_URI` is a local directory or an
  `az://`/`s3://` URI on the same code path (only local has been run).
- **Three search modes** where pgvector has one: vector (cosine), native FTS (BM25), and hybrid
  fused with RRF (K=60), which needs no score calibration between cosine and BM25.
- **Filters are prefilters**, so a selective `where` still returns k hits.
- **Index policy is a decision, not a default:** no vector index below 100,000 rows (a flat scan
  over 1,250 vectors is exact and fast; IVF-PQ needs training data and costs recall). `make bench
  ARGS="--scale 80"` is where the index path gets measured.
- **Idempotent ingest:** `merge_insert` on `call_id`.
- **R3 bench:** 40 labeled queries in four types (topical, filter, exact, paraphrase) with relevance
  derived from generator facts, not from search results. Honest limit: the corpus is templated
  (fewer than 30 distinct texts with names masked), so the numbers compare backends on this corpus
  only. `results/retrieval/` says **not run**: no bench numbers exist yet.
- **DuckDB over the same files:** SQL aggregates and search over the Lance dataset with no export
  step; read-only, L2 distance only, and hybrid uses an alpha blend instead of RRF.

### Semantic layer

Grounded in `olap/dbt/models/marts/semantic/metrics.yml`:

- **No ambiguous metric exists.** There is no `interest_rate`, `balance`, `limit` or `lcv`.
  "Interest rate" maps to seven LOB-filtered metrics, "balance" to several, each a different number.
- **One discriminator:** `product.lob`, surfaced as `Dimension('product__lob')` in each metric's
  filter. `mf query --explain` shows it as a `WHERE product__lob = '…'` per metric (A3).
- **Sign conventions are declared:** `banking_available_balance` is an asset,
  `credit_card_outstanding` a liability, and `net_member_liquidity` is the one declared blend.
- **Conventions are declared once:** `member_lifetime_value = relationship_revenue × 24 /
  active_members`, with the 24 explained in the description. Changing it is a code review.
- **The agent asks instead of guessing:** `agent/data/ambiguous_terms.yml` maps a term to its
  candidates; a bare "rate" interrupts with the options and resumes with the choice.
- **Portable definitions:** the same YAML compiles for Postgres, Snowflake and Databricks targets,
  and exports to Apache Ossie (with its known issues listed in `docs/semantics/README.md`).
- The same concepts (measures, dimensions, entities, filtered and derived metrics) exist in other
  semantic layers; this repo implements them only in dbt + MetricFlow.

### LangChain, LangGraph, LangSmith

- **LangChain:** the agent binds the MCP server's tools through `langchain.mcp` (`MCPAdapter`,
  on fastmcp 4 and mcp 2.x), in `agent/tools.py`. M0 (#18) ported the server to mcp 2.x so this
  could work; `langchain-mcp-adapters` was the planned fallback and is not in the repo. Chat models
  are LangChain chat classes behind `get_llm()`. `langchain.mcp` is in beta and warns on import.
- **LangGraph:** explicit routing (each node calls a named tool), not a tool-calling loop, so a
  deterministic eval can check the route. One `interrupt()` in `clarify`; resume with
  `Command(resume={"choices": [...]})` on the same `thread_id`. `AsyncPostgresSaver` persists the
  paused run, so it resumes after a restart. Served with `langgraph dev` (a dev server, not
  production).
- **LangSmith:** tracing on by env only. Golden set of 51 questions (11 ambiguous, 18 metric,
  10 call lookup, 12 open) whose expectations are computed from committed data. `make evals` runs
  it offline as a CI gate; `make evals-live` runs it as a LangSmith experiment with an
  LLM-as-judge that must differ from the agent's model. `results/evals/` says **not run**.

### Snowflake and Databricks

- **S1, portable models (#21):** Postgres-only SQL replaced with dbt cross-database macros; three
  targets in `profiles.yml`, all from env; Snowflake on key-pair auth with no password field.
  `tests/test_dbt_portability.py` guards it.
- **S2, loaders (#26, #27):** the same OLTP rows `olap/seed.py` puts in Postgres, loaded as
  truncate-and-load (idempotent) with explicit casts per warehouse, and a row-count check per table.
  Types come from `olap/oltp/schema.sql`; an unmapped type raises.
- **I-SF (#24):** Snowflake objects in Terraform, plan only.
- **S3, "one metric, three warehouses" parity (`make parity`): not built yet.** It is the next
  ticket: run `mf query` for five agreed metrics on all three targets, diff to a tolerance, and save
  each target's SQL next to the numbers. Nothing has run on a real Snowflake or Databricks yet.

---

## Known limits and not built yet

- **Not built yet:** S3 parity (`make parity`); fine-tune F2–F6; S3 (AWS) ingestion
  (`rag.pipeline --source s3` raises `NotImplementedError`); CSAT in the vector store; the agent
  talking to the MCP server over HTTP (it still spawns it over stdio); an agent HTTP endpoint (L4).
- **No results yet:** `results/retrieval/` and `results/evals/` both render "not run". The numbers
  in `olap/dbt/README.md` come from a seeded local run, not from `results/`.
- **Never run against a real account:** the Snowflake and Databricks loaders and dbt targets, and
  every Terraform `apply` beyond what you applied yourself.
- `evals/README.md` still lists three known failures (g24, g25, g38); #28 fixed them and
  `evals/known_failures.json` is empty.
- The corpus is templated, so retrieval quality numbers will not transfer to real transcripts.
- `README.md` and `infra/terraform/` still describe the original App Service setup; `infra/azure/`
  replaces it.
