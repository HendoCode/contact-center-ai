# Agent evals (L2)

A golden set of 51 questions for the LangGraph agent (`agent/`), the evaluators that score it, and two runners: offline in CI and live in LangSmith.

```bash
make evals                                          # offline: no keys, no DB, no network
python -m evals.datasets.build_golden --check       # exit 1 if the golden set is stale
make evals-live ARGS="--limit 5"                    # live smoke run: 5 questions (costs money)
make evals-live                                     # live: all 51, real models and tools, to LangSmith
make evals-live DRY=1                               # the live plan and cost estimate; resolves and runs nothing
make evals-compare A=<run name>                     # before/after table: that run against the latest
make evals-export EXP=<LangSmith experiment>       # per-item results of an older run, read-only
make evals-live ARGS="--dataset holdout"            # the held-out set (16 questions)
LLM_MODEL=<openrouter id> make evals-live ARGS="--group open --run open-<model>"   # one group, another agent model
```

## The golden set

`datasets/agent_golden.jsonl`, one question per line:

| kind | n | what it checks |
|---|---|---|
| ambiguous | 11 | A bare "rate", "balance" or "LCV/LTV" must interrupt with the right term and options. The run answers with `clarify_with`, then expects those metrics and SQL. |
| metric | 18 | A question that names a declared metric, or settles a term with one qualifier: no interrupt, the right metric names, and SQL in the answer. |
| call_lookup | 10 | One call by id: route `summarize_call` and cite that id. Includes one lowercase id and one id past the end of the corpus. |
| open | 12 | Answered from transcripts: route `retrieve`, with citations that exist in the corpus. |

Only the questions are written by hand. `datasets/build_golden.py` computes every expectation from committed data and never runs or imports the agent:

- **Metric names** come from `olap/dbt/models/marts/semantic/metrics.yml`. A question names a metric when it contains the metric's label ("Avg" read as "average", any parenthetical and "%" dropped).
- **Interrupts** come from `agent/data/ambiguous_terms.yml`, using the semantics documented in that file. A term is settled by a candidate named by label, or by exactly one candidate with a qualifier in the question. Otherwise the agent should ask.
- **Call ids and facts** come from the seed-42 generator's `transcripts.json`. Each id is picked as the lowest one matching a predicate. The facts become the judge's reference.

Each record also carries `reference` (what a correct answer rests on, for the judge) and `derivation` (how its expectations were computed).

## Evaluators

`evaluators.py`. Each one takes LangSmith's `(inputs, outputs, reference_outputs)` signature, so the same function scores both runs. The score is true or false, or none when the check does not apply to the item.

| key | passes when |
|---|---|
| `route` | the graph's route equals the golden route |
| `interrupt` | an interrupt fired exactly when expected, with the same terms and option sets |
| `metric` | the output's `metric_names` equal the expected set |
| `sql` | the output has SQL and the answer text contains it |
| `citations` | citations are present when expected, and every call id in the answer exists in the corpus |
| `call_id` | a lookup cites the named call; for a missing call, it reports that id and cites nothing |
| `judge` | live only: an LLM-as-judge score of 1–5, scaled to 0–1, using the versioned prompt `prompts/judge_answer_v1.md` |

## Offline: `make evals`

The real graph runs on the doubles in `offline.py`. The call-id shortcut, the summarize-to-retrieve fallback, the term interrupt and resume, metric resolution, SQL extraction, grounding and answer formatting are all the agent's own code. Model decisions are scripted from the golden item: the classifier returns the golden route, `ask_the_analyst` resolves to the golden metrics, and search cites the lowest call ids in the golden categories. Offline scores therefore measure the graph's deterministic behavior. Only `make evals-live` measures the model.

The run fails on any failure not listed in `known_failures.json`, and on any listed failure that now passes. The same gate runs in `make test` (`tests/evals/`) and in CI. The listed failures are findings about the agent, not about the golden set:

- **g24, g25:** naming `banking_ledger_balance` or `checking_available_balance` by label still interrupts, because two candidates tie on qualifier votes.
- **g38:** a lowercase call id is not recognized, because `CALL_ID_RE` is case-sensitive.

LangSmith tracing is forced off for the offline run.

## Live: `make evals-live`

This needs:

- the stack: `make up`, then `make dev-data`, which builds every piece in order (synthetic JSON, OLTP schema, raw load, vector-store ingest, then the dev dbt build of the marts and the semantic manifest `mf` reads). Rerun it after any database reset; every step is idempotent;
- the 1Password CLI, signed in (`eval $(op signin)`), and two references to items in your vault:

  ```bash
  export LANGSMITH_KEY_REF='op://<your vault>/<LangSmith item>/<field>'        # becomes LANGSMITH_API_KEY
  export EVALS_OPENAI_KEY_REF='op://<your vault>/<OpenRouter item>/<field>'    # becomes OPENAI_API_KEY
  ```

  `OPENAI_API_KEY` is an OpenRouter key: it pays for the agent (`LLM_PROVIDER=openai`, `LLM_MODEL`) and the judge. The repo names no vault; `tools/op/evals.env` only says which variable holds each reference.

`make evals-live` runs `tools/evals-live-run.sh`, which resolves both keys with `op run` into the run's processes only. The judge goes through OpenRouter: `EVAL_JUDGE_PROVIDER=openai`, `EVAL_JUDGE_MODEL=anthropic/claude-opus-5.5` (OpenRouter's spelling) and `LLM_BASE_URL=https://openrouter.ai/api/v1` are the defaults, and any of them set in the shell wins. Before anything is paid for, it checks, with one message each: both references set and readable, the agent's model unlike the judge's, the semantic manifest built for the local Postgres, every marts table the metric tool queries present (or, when the raw tables are missing too, a separate line saying to load them), Postgres reachable with the transcripts embedded (pgvector), and Ollama reachable when it does the embeddings. Then it prints an estimated cost range from OpenRouter's live per-token prices for the two models, under a stated assumption of tokens per question (`ASSUMPTIONS` in `preflight.py`); a model not served through OpenRouter is listed as not priced. `DIRECT=1` skips 1Password and the checks and reads everything from the shell or `.env`, as before.

The run itself still refuses to start when the agent and judge models match. It syncs the golden set to the LangSmith dataset `ccai-agent-golden-<sha8>`, named after a hash of the file's content and keyed by golden id, so a rerun adds nothing twice. It then runs one experiment with every evaluator plus the judge, writes `results/evals/<UTC date-time>_<run>_<short git sha>.json`, points `results/evals/LATEST` at it, and renders `results/evals/README.md`. A results file is never overwritten: a second run in the same second gets a `-2` suffix. The run name defaults to `agent-golden-<LLM_PROVIDER>`; `ARGS="--run post-fix"` names it. Each file also keeps every item's outputs (answer, route, metrics, SQL, citations), its scores and the judge's comment under `items`, so a run can be diagnosed without LangSmith. `ARGS="--limit 5"` scores the first five questions.

## Held-out set

`datasets/agent_holdout.jsonl` holds 16 questions the golden set does not have (ambiguous 4, metric 5, call_lookup 3, open 4), in the golden schema with ids `h01`..`h16`. They were written once, on 2026-10-05, from the synthetic data and the semantic layer only (metric labels, the ambiguous terms, the transcript categories and outcomes), before any score on them was seen; the questions are in `datasets/holdout_specs.py` and every expectation is derived by the same code as the golden set (`python -m evals.datasets.build_golden --dataset holdout [--check]`).

**The file is held out.** Its questions are not edited after scores are seen. A factual error in a question may be fixed; each fix is recorded in `holdout_specs.py` with its date and reason. It has its own content hash and its own LangSmith dataset (`ccai-agent-holdout-<sha8>`). `make evals-live ARGS="--dataset holdout"` runs it; the default run is the golden set, unchanged. `make evals-compare` refuses to compare a golden run with a holdout run (no score there is a before/after) unless `--allow-different-datasets`.

## Trying another agent model

The agent model is configuration: `LLM_MODEL` (any OpenRouter model id, with `LLM_PROVIDER=openai`, the default). The judge stays fixed (`anthropic/claude-opus-5.5`, prompt `judge_answer_v1`), so judge scores stay comparable across agent models. `--group open` (comma-separated, any of `ambiguous`, `metric`, `call_lookup`, `open`) scores only those groups, which keeps a model trial cheap; the estimate counts only those questions. Compare a group-only run against a full run's table for the same group: `make evals-compare A=<full run> B=<group run>` warns that `all` is not comparable and the `open` table is.

Candidates, checked against OpenRouter's public model list on 2026-10-05 (price per million input/output tokens; all three list tool calling and structured outputs, which the agent needs):

| model | price in/out | estimate, open group (12 q, with judge) | why |
|---|---|---|---|
| `deepseek/deepseek-v4-pro` | $0.21 / $0.42 | $0.11 to $0.29 | about the current agent's price, a much larger model: the cheapest real step up |
| `moonshotai/kimi-k2.7-code` | $0.67 / $3.35 | $0.15 to $0.51 | strong at tool use, mid price |
| `anthropic/claude-sonnet-5.5` | $2.00 / $10.00 | $0.26 to $1.08 | the upper bound worth paying for; agent cost dominates |

The current agent is `z-ai/glm-5.3-flash` at $0.15 / $0.50. Estimates use the preflight's stated token assumption per question; open questions carry retrieved transcripts, so their real input may sit near the top of the range. Not verified here: how well each model's tool calling works with this agent's MCP tools (only a live run shows that).

**Planned runs and total estimate** (each prints its own estimate first): the holdout with the current agent, $0.14 to $0.36; the open group with each of the three candidates, $0.11 to $0.29, $0.15 to $0.51 and $0.26 to $1.08. **Total: $0.66 to $2.24**, under the $3 cap.

```bash
make evals-live ARGS="--dataset holdout --run holdout-glm-5.3-flash"
LLM_MODEL=deepseek/deepseek-v4-pro    make evals-live ARGS="--group open --run open-deepseek-v4-pro"
LLM_MODEL=moonshotai/kimi-k2.7-code   make evals-live ARGS="--group open --run open-kimi-k2.7-code"
LLM_MODEL=anthropic/claude-sonnet-5.5 make evals-live ARGS="--group open --run open-claude-sonnet-5.5"
make evals-compare A=post-env-fix B=open-deepseek-v4-pro    # run names; each resolves to its newest file
```

## How the agent searches, and trying another search

An open question routes to `retrieve`, which calls the MCP tool `search_transcripts` with the question. The tool (`ccai_mcp/tools.py`) runs `rag.pipeline.rag_query`: `get_retriever()` picks the store from `RETRIEVER_BACKEND` (`pgvector` by default, or `lancedb`), searches the top `k` transcripts with no filter, and the agent's model writes an answer from those transcripts that cites their call ids. Until this change, `k` was always 5 and the mode always `vector`.

Two settings now control it, with today's behavior as the default:

- `AGENT_RETRIEVAL_MODE` = `vector` (default), `fts` or `hybrid`. It is read by the search tool, which inherits the agent's environment. pgvector serves `vector` only, so `fts` and `hybrid` need `RETRIEVER_BACKEND=lancedb`; the preflight refuses the mismatch with one line.
- `AGENT_RETRIEVAL_K` = how many transcripts the agent asks for (default 5).

Every results file records `retriever_backend`, `retrieval_mode` and `retrieval_k`, and `make evals-compare` shows them and flags a change. Runs recorded before this count as pgvector, vector, 5.

The experiment, with the same agent (`z-ai/glm-5.3-flash`), the same judge and only the open group, is one command:

```bash
make evals-retrieval-experiment DRY=1       # the plan and every run's estimate; runs nothing
make evals-retrieval-experiment             # run it
make evals-retrieval-experiment K10=1       # also LanceDB hybrid with k=10
```

It prints the total estimate first, then in order: the open group on pgvector with vector search (`--run open-pgvector-vector`), the LanceDB ingest (`RETRIEVER_BACKEND=lancedb make ingest`, reusing `.cache/embeddings/`), the open group on LanceDB with hybrid search (`--run open-lance-hybrid`), and `make evals-compare A=open-pgvector-vector B=open-lance-hybrid`. Each run prints its own estimate ($0.11 to $0.27 each; the judge is most of it) and the first failing step stops it with the fix. `K10=1` adds `open-lance-hybrid-k10` and a k=5 against k=10 comparison; it reads twice the transcripts, so allow up to about $0.35 for it. `make` adds the `lance` dependency group whenever `RETRIEVER_BACKEND=lancedb`. Before reading the deltas, check that each table's `tool err` row reads 0.

## Comparing runs: `make evals-compare`

```bash
make evals-compare A=open-pgvector-vector B=open-lance-hybrid    # run names: the newest file of each --run
make evals-compare A=latest~1 B=latest                            # the two newest runs
make evals-compare A=results/evals/baseline-2026-10-05-pre-fix.json   # a file path also works; B: latest
```

It prints which file each side resolved to (a run name means the newest file of that `--run`; two of the same name within one minute are refused as ambiguous), then what each side ran (agent model, judge model, judge prompt, golden set hash, `--limit`), then one table per group (`all`, then each kind) with every check's score before, after, and the delta. It warns when any of those differ. A different judge model or prompt makes the judge column incomparable; a different golden set or limit makes every column incomparable; a different agent model is what the delta then measures.

For a run made before results files kept `items`, `make evals-export EXP=<experiment>` reads that LangSmith experiment (read-only) and writes the same per-item detail to `results/evals/items/<experiment>.json`: golden id and kind, inputs, the agent's outputs, the run error, and every evaluator's score and comment, including the judge's reasoning. It needs `LANGSMITH_API_KEY`, taken from 1Password when `LANGSMITH_KEY_REF` is set and otherwise from the shell or `.env`, and it never overwrites a file (`OUT=<file>` picks another).

## Findings

### 2026-10-05 baseline: the metric answers were MetricFlow errors (environment, not agent)

The first full live run (`results/evals/baseline-2026-10-05-pre-fix.json`, LangSmith experiment `agent-golden-openai-74042631`, agent `z-ai/glm-5.3-flash`, judge `anthropic/claude-opus-5.5`) scored route, interrupt, metric and call_id at 1.00, but sql at 0.00 and the judge at 0.16 (ambiguous) and 0.19 (metric). Its per-item export shows why: every one of the 29 ambiguous and metric items answered with a `query_metric` error, not a result: a Postgres `syntax error at or near` a backtick in `` FROM `ccai`.`marts`.`f_account_snapshot` ``.

The cause was the environment. `olap/dbt/target/semantic_manifest.json`, which `mf` reads, had last been written by `dbt build --target databricks`, which quotes with backticks. The live run's metric tool then ran that Databricks SQL against the local Postgres. The route, interrupt and metric checks compare names only, so they passed; the answer text held no result and no SQL, so sql and the judge failed.

**The baseline is invalid for the ambiguous and metric groups.** Its call_lookup and open scores stand. The comparison that means something is this baseline (manifest poisoned) against a rerun with a correct dev manifest, read with `make evals-compare`.

What changed so it cannot recur (no change to the golden set, the judge prompt or the agent's answer text):

- `make dbt-build WAREHOUSE=...` writes its artifacts to `olap/dbt/target/<warehouse>/` (`--target-path`), so a warehouse build no longer overwrites the dev `target/semantic_manifest.json`. The warehouse commands in `olap/dbt/README.md` show the same flag. Per-target directories were chosen over moving the dev manifest because `mf` has no flag for a manifest path and always reads `target/`.
- `query_metric` checks the manifest before running `mf` and refuses with one line naming the adapter it was built for and the fix (`cd olap/dbt && uv run --group dbt dbt parse`). The adapter comes from `target/manifest.json` metadata, or from backtick-quoted relation names when that file is absent. The `make evals-live` preflight runs the same check, so a poisoned manifest stops the run before anything is paid for.
- Results files and exports replace `/home/<user>/` and `/Users/<user>/` with `~/`, because tool errors quote absolute paths and these files are committed to a public repo.

### 2026-10-05 first rerun: the marts were never built

After `dbt parse` fixed the manifest, the rerun's 29 ambiguous and metric items failed again, now with `relation "marts.f_transaction" does not exist`. Local Postgres had been re-seeded (`make seed ingest`), which creates the OLTP tables, but `dbt build` had not run for the dev target, so the `marts` schema the metric tool queries did not exist. This is a second environment cause that invalidates a run for those two groups, independent of the first.

What changed: `make dbt-build-dev` builds the dev target as a first-class step, and the run sequence above lists it after seed and ingest. The `make evals-live` preflight now confirms that every table named in the semantic manifest's relations exists in the dev Postgres, and stops with `marts not built (<tables> missing): run 'make dbt-build-dev' ...` before anything is paid for.

### 2026-10-05 second rerun attempt: the raw tables were absent

`make dbt-build-dev` then failed 16 of 111 nodes with `relation "public.account" does not exist`. On the host, `make seed` only generates the JSON and `make ingest` only embeds; the OLTP schema (`olap/oltp/apply.sh`) and the raw load (`olap/seed.py`) ran only inside the Compose `seed` container, so after the database was reset nothing on the host recreated the raw tables. This is the third environment cause.

What changed: `make dev-data` runs every step on the host in order (generate, schema, load, ingest, dbt build), prints one line per step, and stops at the first failure with its fix. The preflight now tells the two cases apart: raw tables absent says to run `make dev-data` (or `olap/oltp/apply.sh` then `uv run python olap/seed.py`), and raw tables present but marts absent says `make dbt-build-dev`.

### 2026-10-05 open questions: what the judge marks down

After the environment fixes, the open group scored 0.58 on the golden set and 0.62 on the held-out set, and swapping the agent model did not lift it (deepseek-v4-pro 0.50, kimi-k2.7-code 0.54, claude-sonnet-5.5 0.48, glm-5.3-flash 0.58; single runs of 12). The judge's reasoning on the 12 golden open items in `2026-10-05T045841Z_post-env-fix_657fe1e.json`:

| item | judge | what the judge marks down |
|---|---|---|
| g41 fees | 0.00 | the search tool returned an error, not transcripts (below) |
| g51 card services | 0.25 | five calls described as "all five", one request type claimed for 114 calls |
| g40 fraud | 0.50 | generalizes from five of 143 calls; speculates about a shared cause |
| g43 balance | 0.50 | "all five calls" of 179, no sample caveat; automation advice beyond the data |
| g48 escrow | 0.50 | "1 of 5 resolved" read as the category's rate; some speculation |
| g42, g44, g45, g46, g47, g49, g50 | 0.75 | grounded and responsive; each marked down only for generalizing from 5 calls without saying it is a sample, sometimes with mild speculation |

Classified:

- **Retrieval found the right calls.** All 55 cited calls in that run are in the item's expected category, and the judge never says a relevant call was missed. The one exception is g41: in the kimi and sonnet runs, "Why do members call to dispute fees?" retrieved five `fraud_dispute` calls (0 of 5 on category), a real retrieval miss that hybrid search with its keyword match is the natural test for.
- **The answer is a thin sample presented as the whole (11 of 11 scored items).** With `k=5`, every answer rests on five transcripts and most say "all five" or "every call". The reference states each category's size (15 to 179 calls), so the judge marks the coverage gap every time. That is the dominant cause, and it points at `k` and at the answer not saying it read a sample, more than at the store or the model.
- **A search-tool error (`Table 'langchain_pg_collection' is already defined for this MetaData instance`)** replaced the answer on g41 here and on 7 of the 36 items in the three model runs (deepseek 3, kimi 2, sonnet 2), each scored 0 or near it. That alone moves a 12-item group mean by up to 0.25 and explains more of the model-to-model spread than the models do. **Fixed:** `langchain_postgres` defines its tables on the first `PGVector` it builds behind an unlocked check, so two searches starting at once in the MCP server (which runs tools in threads) both defined them and the later ones failed; reproduced on Postgres with 4 concurrent searches (2 to 3 of 4 failed). `rag.embeddings.get_vector_store` now builds stores under one lock and reuses the default store per process. Tool failures now read `TOOL ERROR (<tool>): ...`, results files mark each such item (`tool_error`) and count them per group (`tool_errors`), and `make evals-compare` prints the count per group and warns when a run has any, also for runs recorded before this. Rerun the open group before reading model or search deltas.
- **No case reads as a harsh judge or a wrong reference.** The judge is consistent: grounded but over-generalized answers get 4 of 5, and stronger generalization gets less.

The golden and held-out sets, the judge prompt and the answer text are unchanged.

### 2026-10-05 open answers now state their sample

Neither a stronger agent model nor LanceDB hybrid search lifted the open group (pgvector vector 0.62, LanceDB hybrid 0.48, with 0 tool errors), which left the framing finding above: answers drawn from 5 retrieved calls described them as the whole category. The search tool (`rag_query`) now counts each retrieved category in the same store the search used (`Retriever.count(where)`, pgvector and LanceDB) and opens every answer with a plain statement of what it read, for example `Based on a sample of 5 retrieved calls: 5 of 143 calls in fraud_dispute.` The prompt also tells the model the transcripts are a sample and not to say "all calls". The sizes come from the store, never from the evals' references; a size that cannot be counted is reported as unavailable. The golden and held-out sets, the judge prompt and the references are unchanged.

To measure it, rerun the open group on both sets and compare by run name ($0.11 to $0.27 and $0.04 to $0.09):

```bash
make evals-live ARGS="--group open --run open-framing"
make evals-live ARGS="--dataset holdout --group open --run holdout-open-framing"
make evals-compare A=open-pgvector-vector B=open-framing
make evals-compare A=holdout-glm-5.3-flash B=holdout-open-framing     # read the open table
```

