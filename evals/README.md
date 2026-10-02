# Agent evals (L2)

A golden set of 51 questions for the LangGraph agent (`agent/`), the evaluators that score it, and two runners: offline in CI and live in LangSmith.

```bash
make evals                                          # offline: no keys, no DB, no network
python -m evals.datasets.build_golden --check       # exit 1 if the golden set is stale
make evals-live                                     # real models and tools, to LangSmith (costs money)
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
- the agent's provider keys;
- `LANGSMITH_API_KEY`;
- a judge that differs from the agent's model: `EVAL_JUDGE_PROVIDER` (`anthropic` or `openai`) and `EVAL_JUDGE_MODEL` (default `claude-opus-5-5`). The run refuses to start when the two models match.

It syncs the golden set to the LangSmith dataset `ccai-agent-golden-<sha8>`, named after a hash of the file's content and keyed by golden id, so a rerun adds nothing twice. It then runs one experiment with every evaluator plus the judge, writes `results/evals/<date>_agent-golden-<provider>.json`, and renders `results/evals/README.md`. Pass `ARGS="--limit 5"` for a cheap smoke run.
