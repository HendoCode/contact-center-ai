# Agent evals (L2)

A golden set of 51 questions for the LangGraph agent (`agent/`), the evaluators that score it, and two runners: offline in CI and live in LangSmith.

```bash
make evals                                          # offline: no keys, no DB, no network
python -m evals.datasets.build_golden --check       # exit 1 if the golden set is stale
make evals-live ARGS="--limit 5"                    # live smoke run: 5 questions (costs money)
make evals-live                                     # live: all 51, real models and tools, to LangSmith
make evals-live DRY=1                               # the live plan and cost estimate; resolves and runs nothing
make evals-compare A=results/evals/<before>.json    # before/after table against the latest run
make evals-export EXP=<LangSmith experiment>       # per-item results of an older run, read-only
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

- the stack: `make up`, `make seed`, `make ingest`, and `dbt build` in `olap/dbt`;
- the 1Password CLI, signed in (`eval $(op signin)`), and two references to items in your vault:

  ```bash
  export LANGSMITH_KEY_REF='op://<your vault>/<LangSmith item>/<field>'        # becomes LANGSMITH_API_KEY
  export EVALS_OPENAI_KEY_REF='op://<your vault>/<OpenRouter item>/<field>'    # becomes OPENAI_API_KEY
  ```

  `OPENAI_API_KEY` is an OpenRouter key: it pays for the agent (`LLM_PROVIDER=openai`, `LLM_MODEL`) and the judge. The repo names no vault; `tools/op/evals.env` only says which variable holds each reference.

`make evals-live` runs `tools/evals-live-run.sh`, which resolves both keys with `op run` into the run's processes only. The judge goes through OpenRouter: `EVAL_JUDGE_PROVIDER=openai`, `EVAL_JUDGE_MODEL=anthropic/claude-opus-5.5` (OpenRouter's spelling) and `LLM_BASE_URL=https://openrouter.ai/api/v1` are the defaults, and any of them set in the shell wins. Before anything is paid for, it checks, with one message each: both references set and readable, the agent's model unlike the judge's, Postgres reachable with the transcripts embedded (pgvector), and Ollama reachable when it does the embeddings. Then it prints an estimated cost range from OpenRouter's live per-token prices for the two models, under a stated assumption of tokens per question (`ASSUMPTIONS` in `preflight.py`); a model not served through OpenRouter is listed as not priced. `DIRECT=1` skips 1Password and the checks and reads everything from the shell or `.env`, as before.

The run itself still refuses to start when the agent and judge models match. It syncs the golden set to the LangSmith dataset `ccai-agent-golden-<sha8>`, named after a hash of the file's content and keyed by golden id, so a rerun adds nothing twice. It then runs one experiment with every evaluator plus the judge, writes `results/evals/<UTC date-time>_<run>_<short git sha>.json`, points `results/evals/LATEST` at it, and renders `results/evals/README.md`. A results file is never overwritten: a second run in the same second gets a `-2` suffix. The run name defaults to `agent-golden-<LLM_PROVIDER>`; `ARGS="--run post-fix"` names it. Each file also keeps every item's outputs (answer, route, metrics, SQL, citations), its scores and the judge's comment under `items`, so a run can be diagnosed without LangSmith. `ARGS="--limit 5"` scores the first five questions.

## Comparing runs: `make evals-compare`

```bash
make evals-compare A=results/evals/<before>.json B=results/evals/<after>.json
make evals-compare A=results/evals/<before>.json            # B: the file LATEST names
```

It prints what each side ran (agent model, judge model, judge prompt, golden set hash, `--limit`), then one table per group (`all`, then each kind) with every check's score before, after, and the delta. It warns when any of those differ. A different judge model or prompt makes the judge column incomparable; a different golden set or limit makes every column incomparable; a different agent model is what the delta then measures.

For a run made before results files kept `items`, `make evals-export EXP=<experiment>` reads that LangSmith experiment (read-only) and writes the same per-item detail to `results/evals/items/<experiment>.json`: golden id and kind, inputs, the agent's outputs, the run error, and every evaluator's score and comment, including the judge's reasoning. It needs `LANGSMITH_API_KEY`, taken from 1Password when `LANGSMITH_KEY_REF` is set and otherwise from the shell or `.env`, and it never overwrites a file (`OUT=<file>` picks another).
