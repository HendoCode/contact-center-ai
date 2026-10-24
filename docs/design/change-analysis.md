# Change analysis (E0)

Supervisors ask why a number moved. An answer needs a governed metric over two windows and cited evidence from the calls between them. This note specifies events with known answers, the `explain_change` route, and the contract for E1–E7. Windows are half-open ISO dates (`start` inclusive, `end` exclusive).

## Event model

`data/synthetic/events.yml` is data. Event calls are added to the 1,250 baseline calls from a separate stream seeded from 42, so baseline draws don't change. After the merge, interaction, survey and transaction ids are reassigned in timestamp order, so no id range marks an event. Calls per phase and category = `round((multiplier − 1) × baseline weekly rate × weeks)`, with the rate measured on the generated baseline population. An event call is scored like a baseline call, then shifted by `csat_shift`. The whole part always applies; one more point is added with probability equal to the fraction. Scores are clamped to 1–5.

```yaml
version: 1
branches: {Westside: 0.20, Downtown: 0.30, Northgate: 0.25, Riverside: 0.25}   # member.home_branch shares
events:
  - id: fee_schedule_change          # ids appear only under ground_truth/
    split: golden                    # golden | heldout
    date: 2026-04-15
    announcement: {name: Fee schedule change, aliases: [fee schedule change, fee change],
                   text: "Overdraft fee rises from $29 to $35; courtesy waiver once per 12 months."}
    population: {category: [fee_dispute, account_balance], home_branch: null}
    phases:
      - {weeks: 6, volume: {fee_dispute: 8.0, account_balance: 1.25}, csat_shift: -1.0}
      - {weeks: end, volume: {fee_dispute: 3.0}, csat_shift: -0.6}
    drivers:                         # keywords: read only by the isolation test
      - {id: unaware_of_change, share: 0.45, fee_type: overdraft, keywords: [...]}
      - {id: courtesy_refund_refused, share: 0.25, fee_type: overdraft,
         patterns: [{id: inconsistent_waiver_explanation, share: 0.40, variants: [once_a_year, once_ever, partial_refund]}]}
      - {id: multiple_overdrafts_same_day, share: 0.20, fee_type: overdraft, ledger: {overdraft_fees: [2, 4]}}
      - {id: other_fee_question, share: 0.10, fee_type: {nsf: 0.4, monthly_maintenance: 0.4, atm: 0.2}}
```

| Event (split) | Date | Population | Volume × (phase) | CSAT shift/call | Drivers % | ≈ calls |
|---|---|---|---|---|---|---|
| Fee schedule change (golden) | 04-15 | `fee_dispute`; `account_balance` | 8 (6 wk), 3 (to end); balance 1.25 (6 wk) | −1.0, −0.6 | didn't know 45 · refund refused 25 · several overdrafts in a day 20 · other fee 10 | 110 |
| Card fraud rule (golden) | 06-15 | `card_services`, `fraud_dispute` | 3 (3 wk), 1.5 (5 wk) | −0.9, −0.45 | new-merchant decline 50 · verification text 30 · subscription decline 20 | 85 |
| Mobile app v5 (heldout) | 07-20 | `online_banking_support` | 5 (2 wk), 1.5 (4 wk) | −0.6, −0.3 | biometric re-enroll 55 · passcode 30 · bill-pay payees 15 | 55 |
| Westside closure (heldout) | 08-07 | Westside members in `account_balance`, `account_opening`, `payment_assistance` | 6 (4 wk, cut at data end) | −0.8 | cash/coin 40 · safe deposit 35 · notary/cashier's check 25 | 50 |

`home_branch` comes from a hash of `member_id`. Fee transactions carry `fee_type` and are charged at schedule prices:

- overdraft: $29, then $35 from the fee change
- NSF: $29
- ATM: $3
- monthly maintenance: the account's fee, or $5 when that fee is zero
- `other`: fees on non-banking accounts

Banking fees split by hash: overdraft 40%, maintenance 35%, NSF 15%, ATM 10%. The baseline amount is still drawn, then replaced, so the random stream stays aligned.

## How drivers are planted and in what proportions

- **Quotas, not draws.** Labels are allocated by largest remainder per event, phase and category, then shuffled. Patterns are allocated the same way inside their driver. Every driver gets at least 6 calls in the default window.
- **Dialogue.** At least three variants per driver (`DRIVER_DIALOGUES[event][driver]`) and per baseline category, picked by a third seeded stream. Variants change wording, never facts, and never name the agent. Account numbers are masked to the last four digits.
- **Ledger agrees.** For `multiple_overdrafts_same_day`, the generator posts 2–4 new-price overdraft fees on the caller's checking account 1–5 days before the call.
- **Coaching.** In 40% of refused-refund calls, the agent says "once a year", "once ever", or offers a partial refund, instead of stating the written rule. Each of these calls has a distinct `staff_id`, so the pattern spans the team.
- **Hygiene.** Labels and splits exist only in `ground_truth/`. They never appear in transcripts, metadata, the vector store, OLTP or the semantic layer. `home_branch` and `fee_type` are facts about members and transactions, so they are filterable. `change_log.json` holds the public announcements only.

## Ground-truth file format (`data/synthetic/ground_truth/events.json`)

Generator output, gitignored like the rest. The values shown here only illustrate the shape.

```json
{"version": 1, "seed": 42, "events_yml_sha256": "…", "data_range": {"start": "2026-03-03", "end": "2026-09-02"},
 "events": [{"id": "fee_schedule_change", "split": "golden", "date": "2026-04-15",
   "population": {"category": ["fee_dispute", "account_balance"], "home_branch": null},
   "phases": [{"start": "2026-04-15", "end": "2026-05-27", "planned": {"fee_dispute": 60, "account_balance": 10}}],
   "default_window": {"filters": {"category": ["fee_dispute"]},
     "before": {"start": "2026-03-04", "end": "2026-04-15"}, "after": {"start": "2026-04-15", "end": "2026-05-27"},
     "metrics": {"call_volume": {"before": 0, "after": 0}, "csat_average_score": {"before": 0.0, "after": 0.0},
                 "responses": {"before": 0, "after": 0}},
     "drivers": [{"id": "unaware_of_change", "before": [], "after": ["CALL-…"]}],
     "patterns": [{"id": "inconsistent_waiver_explanation", "driver": "courtesy_refund_refused",
                   "variants": {"once_a_year": ["CALL-…"], "once_ever": [], "partial_refund": []}}]}}],
 "calls": {"CALL-…": {"event": "fee_schedule_change", "phase": 0, "driver": "unaware_of_change", "pattern": null, "variant": null}}}
```

`calls` lists event calls only. Evaluators recompute expectations for the windows an answer actually reports, from `calls`, `transcripts.json` and `csat.json`. They follow the semantic layer's time rules: `call_volume` by `started_at`, CSAT by `surveyed_at`. `default_window.metrics` must equal MetricFlow on Postgres to two decimals.

## The `explain_change` route (nodes, state, interrupts)

`classify` gains a fourth route. It takes "why did X change after, since or around Y", "did X recover", and follow-ups on a stored change. All 67 existing eval questions keep their routes. `agent/subgraphs/change.py` shares parent state and inherits the parent's checkpointer, as `analyst` does.

```
classify → explain_change[frame → resolve_measure ⇄ clarify → resolve_subject → resolve_when → analyze] → ground → answer
           (follow-up with a stored change: frame → analyze, mode=coaching)
```

| Node | Behavior |
|---|---|
| `frame` | One structured LLM call returns a `ChangeFrame` of phrases |
| `resolve_measure` | Works on the measure phrase only. It tries, in order: a label, the synonyms in `agent/data/change_measures.yml`, then `agent.terms.match_terms`. If two or more supported candidates remain, it goes to `clarify`. With no measure phrase it uses `call_volume` and says so. It refuses anything that doesn't resolve to exactly one supported metric |
| `clarify` | The analyst's node, unchanged (interrupt contract v1, `clarify_metric`). It is the only interrupt. No current term has two supported candidates, so tests use a fixture term |
| `resolve_subject` | Maps the subject to categories (`agent/data/change_subjects.yml`, all 14) or to branches (`data/synthetic/branches.json`). It refuses an unknown subject |
| `resolve_when` | Tries a `change_log.json` alias (uses its date), then an explicit date, then a named period (sets `search_range`). Otherwise it detects over the full range |
| `analyze` | Calls MCP `explain_change` with explicit arguments. It writes `change`, `tool_output` (the tool's `text`), `sql` (`-- before`/`-- after`), `metric_names` and `citations` |

```python
Route = Literal["retrieve", "resolve_metric", "summarize_call", "explain_change"]
class ChangeFrame(TypedDict):
    measure: str | None; subject: str | None; when: str | None
    date: str | None; period: dict | None; follow_up: bool
# AgentState += change_frame, change_filters: dict, change_point: str | None, change_search: dict | None, change: dict | None
# AgentOutput += change: dict | None
```

`classify` resets every per-run key except `change`, so a follow-up on the same `thread_id` reuses the evidence. `ground` requires both SQLs, a declared metric, and citations inside the evidence windows. It rejects any `AGT-` id.

**MCP tool `explain_change`** is the sixth tool, in `ccai_mcp/change.py`. Its signature is `explain_change(metric: str, filters: dict | None = None, change_point: str | None = None, search_range: dict | None = None, window_days: int = 42, question: str | None = None, mode: Literal["drivers", "coaching"] = "drivers", groups: list[dict] | None = None) -> dict`, and the input schema mirrors it. The module also provides `render_change(result) -> str`.

- `filters` is `{"category": [...], "home_branch": [...]}`.
- `window_days` ranges from 14 to 91.
- `groups` is `[{"label", "call_ids"}]` and is used only in coaching mode.

Supported metrics: `call_volume` (a sum), `csat_average_score` (weighted by `responses`), and `first_contact_resolution_rate` and `average_handle_time` (both over `call_volume`). Any other metric returns a `TOOL ERROR` that lists these four. The tool returns this JSON:

```json
{"version": 1, "mode": "drivers", "metric": "csat_average_score", "filters": {"category": ["fee_dispute"]},
 "change_point": {"date": "2026-04-15", "source": "argument|detected", "logged_change": "Fee schedule change", "shift": null},
 "windows": {"before": {"start": "…", "end": "…", "value": 0.0, "n": 0, "sql": "…"},
             "after": {"start": "…", "end": "…", "value": 0.0, "n": 0, "sql": "…", "weekly": [{"week": "…", "value": 0.0, "n": 0}]}},
 "population": {"before": 0, "after": 0},
 "drivers": [{"label": "…", "definition": "…", "before": {"n": 0, "call_ids": []}, "after": {"n": 0, "call_ids": []},
              "citations": [{"call_id": "CALL-…", "quote": "verbatim transcript text"}]}],
 "patterns": [], "unassigned": {"before": 0, "after": 0}, "limits": ["…"], "text": "rendered answer"}
```

- **Detection** runs when no change point is given. At each week boundary *t* in `search_range`, it compares the pooled metric over [*t* − 13 wk, *t*) with [*t*, *t* + 4 wk). Pooling uses the weekly series and its weight. Boundaries with fewer than 20 records on either side are skipped. The largest absolute shift wins, the earliest on a tie. A logged change within 7 days replaces *t*. If no boundary qualifies, the tool says so and returns no drivers.
- **Windows** are [cp − L, cp) and [cp, cp + L), where L = min(`window_days`, the room available on each side). Each window runs two `query_metric_raw` calls: one for the value and SQL, one for the weight.
- **Drivers.** `population` is `Retriever.count(where)`. The tool fetches every call in the window with `search(k=population, where=…)`; above 200 calls it takes a deterministic sample and records that in `limits`. One LLM call proposes up to 6 drivers from at most 30 after-window calls. Batches of 20 then assign each call a driver (or none) and a quote. Code does the counting, drops ids from outside the batch, and keeps up to 3 quotes per driver, each one verbatim from the transcript. Coaching mode does the same over agent turns inside `groups`. Staff ids never reach the model or the output.

## What an answer must contain

`render_change` composes the answer without an LLM. The only model text in it is driver labels and quotes that code has checked.

1. The metric's name and label, the filters, and both windows with their dates.
2. The before and after values with `n`, the change, and each window's SQL. Numbers come only from MetricFlow and `count`.
3. How the change point was chosen (from the question, the change log, or detection with its shift) and any logged change it coincides with. For "did it recover", the weekly values.
4. Each driver as "n of M calls in the after window", with its before-window count and up to three citations.
5. Limits: survey counts, clipped windows, sampling, unassigned calls. Always this one: the timing is consistent with the change, but the data can't show causation.
6. No per-agent scores, names or ids. Coaching answers report patterns across calls as "n of M".

A refusal names the phrase that didn't resolve, lists what can be explained, and calls no tool.

## Eval plan (golden event types vs held-out event types)

Events 1 and 2 are golden. Events 3 and 4 are held out: no golden rows, prompt examples, fixtures or tuning. PRs that change a prompt cite golden numbers only. Held-out scores are reported, never optimized.

- **Golden questions** (`evals/datasets/change_specs.py`):
  - "Why did satisfaction on fee calls drop after the fee schedule change?"
  - "What are members saying about fees since April?"
  - "What should supervisors coach on for fee calls?" (follow-up)
  - "Why did card calls spike in June?"
  - "What are members upset about with declined cards?"
  - "Did satisfaction on card calls recover after the fraud rule change?"
- **Held-out questions** (`change_holdout_specs.py`, fixed here before any code exists):
  - "Why did online banking calls go up in late July?"
  - "What changed for members after the app update?"
  - "Why is satisfaction down for Westside members?"
  - "What do members need since the Westside branch closed?"
- **Rows.** `build_change.py --check` derives every expectation from the ground truth into `change_golden.jsonl` and `change_holdout.jsonl`. Each row is `{"id", "kind": "change", "event", "question", "follow_up_of", "route", "metric_names", "filters", "change_point", "change_point_tolerance_days": 7, "expect_interrupt", "reference", "derivation"}`.
- **Deterministic evaluators**, computed for the windows reported:
  - `change_point`: within the tolerance.
  - `before_after`: equal to two decimals.
  - `driver_recall`: a planted driver with at least 5 calls in the reported scope counts as found when one reported driver holds at least 50% of its calls at 60% or better precision. Matching is by call id, never by wording.
  - `driver_share`: within 10 points.
  - `pattern_recall`: same test as `driver_recall`.
  - `no_agent_ids`.

  These sit next to the existing `route`, `interrupt`, `sql`, `citations` and the live judge.
- **Isolation.** `build_change` refuses a golden spec on a held-out event. A test fails if a held-out driver keyword appears in `agent/prompts`, `agent/data`, the `ccai_mcp/change.py` prompts or a golden dataset. Offline doubles use golden events only.
- **Runs.** `make evals` gates the golden change rows offline. Held-out rows run live only. Results follow §4.3, with `metrics.golden` and `metrics.heldout` reported separately.

## Changes this design makes to the spec it was given

1. **Fee change moved from Apr 1 to Apr 15.** The data starts Mar 3, so Apr 1 allows only a 29-day before window holding 8 fee calls.
2. **Higher multipliers** (fee 3 → 8, cards 2.5 → 3, app 2 → 5, Westside 1.8 → 6), with CSAT shifted per event call. At about 1.4 fee calls a week, ×3 adds about 17 calls in six weeks. That leaves about 2 for the 10% driver and 2 for the coaching pattern. The new values give every driver at least 6 calls and keep the stated shifts after baseline dilution.
3. **Quotas.** Planted counts are exact, and the ±5-point tolerance applies only inside windows.
4. **Additive events and renumbered ids.** The dataset grows to about 1,550 calls. E1 reruns the `--check` builders and fixes the count in `CLAUDE.md`.
5. **A structured `where` instead of a raw MetricFlow string.** A raw string puts SQL into the governed layer. `eq`/`in` filters on named dimensions can be validated and render the same on all three warehouses.
6. **The core lives in the tool, and E3 registers it.** The graph reaches every tool over MCP, and MCP clients, which have no interrupt, get the same numbers.
7. **A change log**, so "after the fee schedule change" resolves to a date and detection can name the change it coincides with. It holds only the announcements, written the same way for all four events.
8. **The model labels drivers and code counts them**, so scoring is by call-id overlap.
9. **The portable `where` gains `home_branch`** (`eq`, `in`).
10. **The `fee` term lists `total_fee_revenue`**, with `unless: [fee call, fee calls, fee dispute, fee disputes, dispute fees, fee schedule, fee change]`, so existing fee questions don't start interrupting.
11. **Coaching runs as a `mode`**, not as a seventh tool.

## Follow-on tickets E1 to E7

| Ticket | Deps | Owns (sole editor) | Provides |
|---|---|---|---|
| E1 Event data · `t/e1-event-synthetic-data` | E0 | `data/synthetic/events.yml`, `generate_data.py`, `ground_truth/`; `olap/oltp/schema.sql` (`member.home_branch`, `account_transaction.fee_type`), `olap/seed.py`; `home_branch` in `rag/pipeline.py` metadata and in `retrieval/base.py` `where` plus both backends; `.gitignore`; `CLAUDE.md` count; rebuilt `agent_golden`, `agent_holdout`, `queries.jsonl`; `tests/data/` | The formats above; `change_log.json` = `[{"name", "aliases", "date", "text"}]`; `branches.json` = `[{"name"}]` |
| E2 Metric windows, fee metrics, `fee` term · `t/e2-metric-windows-fee-terms` | E0; data checks after E1 | `ccai_mcp/metrics.py`; `query_csat` in `tools.py`; those two schemas in `server.py`; `olap/dbt/` (`transaction__fee_type`, `member__home_branch`, `interaction__category_code` on every supported metric); `metrics.yml`; `ambiguous_terms.yml` | `query_metric(..., time_window=None, where=None)` and `query_metric_raw(..., *, time_window=None, where=None)`; `time_window={"start","end"}`; `where=[{"dimension", "op": "eq"\|"in", "value"}]`; `query_csat(..., start_date=None, end_date=None)` on `surveyed_at`; `overdraft_fee_revenue`, `nsf_fee_revenue`, `monthly_maintenance_fee_revenue`, `atm_fee_revenue` (43 → 47) |
| E3 Route · `t/e3-explain-change-route` | E1, E2 | `ccai_mcp/change.py`; the sixth tool in `server.py`; `test_server_handshake.py` (six names); `agent/state.py`, `graph.py`, `nodes/classify.py`, `nodes/ground.py`, `prompts/`, `subgraphs/change.py`, `changes.py`, `data/change_*.yml`; `tests/agent/` | The tool and state above; offline stub tests for the route, clarify, detection, refusal and follow-up |
| E4 MCP clients · `t/e4-explain-change-tool` | E3 | `ccai_mcp/http_smoke.py` (`--tool`, `--args`), `ccai_mcp/README.md`, the tool description, a `call_tool` test | A cited answer from any MCP client |
| E5 Evals · `t/e5-change-evals` | E1, E3 | `evals/datasets/change_*`, `evaluators.py`, `offline.py`, `run.py` (`--dataset change`\|`change-holdout`), `known_failures.json`, `evals/README.md` | The evaluator keys above |
| E6 Demo · `t/e6-change-demo` | E3; E4 for step 6 | `Makefile` `demo-change`, `agent/demo_change.py`, `docs/demo-chat.md` | Six timed steps: fee-call CSAT; "What do we make on fees?" (clarify); coaching follow-up; online banking; open a citation; step 1 over MCP. The preflight checks models, data, index and manifest, and prints the fix for each failure |
| E7 Audio per turn · `t/e7-turn-audio` | E1; E3 citation shape | `tools/tts/`, `data/synthetic/audio_index.json` = `{call_id: [{"turn", "start_ms", "end_ms", "path"}]}`, a LanceDB audio column (pgvector stores the path) | A citation resolves to its turn's audio. `get_call_summary` keeps its schema. Check the TTS license first |

E1 and E2 run in parallel. `server.py` changes in dependency order (E2, then E3, then E4), and every other file has a single owner.
