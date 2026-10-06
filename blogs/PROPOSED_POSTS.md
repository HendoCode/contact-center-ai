# Post briefs: B1–B4 (input for drafting)

Status: briefs, not posts. Nothing here is wired into `build.sh` or the series index. `docs/AGENT_HANDOFF.md` (Wave 4) points here. Every number below names the committed file it comes from; re-read that file before a draft quotes it, because a newer run may have replaced it.

Each brief has the same parts so a drafting pipeline can read it: title, subtitle, status, thesis, reader, evidence, outline, must not claim, open items.

Voice for every post: plain, specific, first person. No aphorism openers, no "here's what everyone gets wrong," no setup-then-hero sentences, no "not X, but Y" reveals, no marketing adjectives, no titles that start with "What." Name what was measured, how, and what it didn't cover.

Slots: the 05–12 plan was written in June, before any of this existed. Stephen decides slots; the suggestions below are only suggestions.

---

## B1 · Fine-tune or retrieve? Measuring it on call summaries

- **Subtitle:** LoRA, a hosted fine-tune, a frontier API and RAG on one structured-summary task.
- **Status:** blocked. F1 (the dataset) is done; F2–F6 have no results yet. Don't draft past the outline until F6 renders.
- **Thesis:** for a narrow, well-defined output (a call transcript to a five-field JSON summary), measure when a small fine-tuned model beats prompting a frontier model or retrieving examples, on quality, latency and cost per million tokens. Report where each one wins and where it doesn't.
- **Reader:** engineers deciding whether to fine-tune; applied-AI and inference teams.
- **Evidence:** `models/finetune/README.md`, `docs/design/finetune.md` (task, fairness rules), the 800/150/300 split in `splits.py`, rule-built gold labels (`gold.py`, no model involved) alongside teacher labels; later `results/finetune/` (F6) and the load-test results (F5).
- **Outline:** the task and its schema; how the labels were made and checked; the arms; field-level exact match and judge scores; latency and cost at two traffic shapes; a short decision guide.
- **Must not claim:** fine-tuning done for a customer. Any number before F6 renders. One observed lesson is allowed, worded as observed: a team I worked with chose retrieval over fine-tuning so regulated data never entered model weights. No employer name.
- **Open items:** whether F3 (the hosted fine-tune) runs at all; if it doesn't, the post covers the open-source arm only and says so.

## B2 · One metric, three warehouses

- **Subtitle:** dbt and MetricFlow on Postgres, Snowflake and Databricks, and an export to Apache Ossie.
- **Status:** mostly ready. The models build on all three targets; the Ossie export and its issues are written up. The headline ("the same numbers everywhere") waits on S3 (`make parity`).
- **Thesis:** declare a metric once and compile it for three SQL dialects. Show what the semantic layer absorbed (quoting, casts, date functions), what the interchange format kept, and what it dropped.
- **Reader:** analytics engineers and data-platform teams weighing a semantic layer or an open interchange format.
- **Evidence:** `olap/dbt/README.md` (three targets, credentials through 1Password `op run`, Snowflake key-pair auth), `tools/bootstrap-snowflake.sh` and `bootstrap-databricks.sh`, `docs/semantics/README.md` (Ossie 0.2.0.dev0 export: 11 datasets, 43 metrics, field mapping, known issues). The dialect problem showed up for real in `evals/README.md`: a manifest built for Databricks sent backtick-quoted SQL to Postgres.
- **Outline:** the metrics and the line-of-business discriminator; three targets and how credentials stay out of files; the dialect differences (from S3's saved SQL, once it exists); the Ossie export and its three issues (the LOB filter points at a field the dataset doesn't have; three row counts all export as `SUM(1)`; ratios lose float division); what an interchange file is good for today.
- **Must not claim:** identical numbers across warehouses until `results/semantics/` exists. That issues were reported upstream (they weren't, as of Oct 6). No vendor pitch: describe Ossie and the standards effort; don't argue for a company.
- **Open items:** run S3; decide whether to file the three export issues upstream before publishing (a stronger post if they're filed). Settle the "interest rate" count: `docs/TOUR.md` says five metrics, `docs/semantics/README.md` says four meanings.

## B3 · Reading an agent graph as a statechart

- **Subtitle:** a LangGraph analyst that stops and asks which rate you mean.
- **Status:** ready (L1 and L2 are built and evaluated).
- **Thesis:** an agent with tools is easier to test and explain when its control flow is an explicit graph. Statecharts give a vocabulary for it: states, guarded transitions, hierarchy, and a pause that waits for an outside event. The post maps those ideas onto the graph that exists and is clear about where LangGraph and statecharts part ways.
- **Reader:** engineers building agents; anyone who has drawn a state machine.
- **Evidence:** `agent/graph.py` (`classify → retrieve | resolve_metric | summarize_call → ground → answer`; `resolve_metric` is the `analyst` subgraph), the `clarify` interrupt and the Postgres/SQLite checkpointer (an interrupted run resumes by thread id), `agent/terms.py` (the ambiguous-term list is data, not code), `evals/README.md` (11 ambiguous golden questions; the interrupt check passed on all of them in the first live run).
- **Outline:** the analyst as a drawing first; nodes as states and routing as guarded transitions; the subgraph as a composite state; the interrupt as a wait for an external event, persisted so it survives a restart; how the eval set checks each transition; what's missing compared with a statechart (no orthogonal regions, no history states) and whether this agent needs them. A short backstory paragraph is optional: Stephen built Eclipse tooling (Xtext) for ECharts, the open-source state-machine language from AT&T Labs Research. Stephen decides whether that paragraph names the engagement.
- **Must not claim:** that LangGraph is a statechart implementation. That the evals caught agent regressions: the first live run's failures were environment defects (see post 10). Production use.
- **Open items:** a Mermaid statechart of the graph, generated from or checked against `graph.py`.

## B4 · pgvector and LanceDB on 100,000 calls

- **Subtitle:** recall, latency and the index settings that moved them.
- **Status:** ready (R1–R3). Not in the original plan.
- **Thesis:** the same 40 labeled questions against the same 100,000 transcripts, through one retriever interface, on a laptop. The numbers depend more on index choice and settings than on the database, and a fair comparison takes work.
- **Reader:** engineers choosing a vector store; anyone reading vendor benchmarks.
- **Evidence:** `results/retrieval/README.md` (2026-10-03, x80-ivf-pq run, with hardware and versions). From that run: exact pgvector scan about 873 ms p50 at recall@10 0.80; pgvector HNSW about 3.8 ms but ANN recall 0.50 against the exact scan, and about 71 ms on filtered queries; LanceDB default IVF-PQ about 16 ms at recall@10 0.44, raised to 0.53 with `refine_factor=20`, and about 25 ms on filtered queries; LanceDB BM25 found every exact-phrase question and none of the paraphrases. Also `retrieval/`, the `az://` check (`make lance-azure-check`), and DuckDB reading the Lance dataset (R3 stretch).
- **Outline:** the labels and the four question types; why recall is reported against the labels and against the exact scan; the table; what each index setting traded; the 49x first result and why it was thrown out; what I'd choose at this size and what I'd test at larger sizes.
- **Must not claim:** anything about scale beyond 100,000 rows, multi-node behavior, compaction or versioning (none were measured). The `ivf_flat` result (exact at about 55 ms) until that run is committed to `results/retrieval/`. Present IVF-PQ's default recall as a default tuned for larger datasets, measured here at a small one, not as a flaw.
- **Open items:** commit the `ivf_flat` run; decide whether the agent-level finding belongs here or in post 10: on single runs of 12 open questions, LanceDB hybrid didn't lift the judge score (0.48 vs pgvector's 0.63). Report it as no lift on a small sample, not as a ranking.

---

## Fold into post 10, "Trust, but Verify": the first eval run measured the environment

Evidence: `evals/README.md` (Findings), `results/evals/`. The first full live run of the 51-question golden set scored 0.34 from the judge with SQL at zero. Three environment causes, found one after another: a manifest built for Databricks, marts never built after a reseed, raw tables that only existed inside a container. Each became a preflight check that stops a paid run before it starts. After the fixes the overall judge score was 0.82 (`results/evals/README.md`, post-env-fix). Open questions stayed lower, and changing the agent model (four tried) or the retriever didn't lift them: on single runs of 12, pgvector vector scored 0.63 and LanceDB hybrid 0.48, too few items to call a difference. This replaces most of post 10's June outline with work that exists; keep the drift and SLO sections as next steps.

## Note for post 12, "The Agent Harness"

The placeholder is still accurate. Two additions: the corrections practice (every "Caught" line and reviewer finding goes to Stephen's corrections log), and the eval story above as an agent-run task where review caught that a passing score meant nothing.
