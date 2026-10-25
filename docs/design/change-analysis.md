# Change analysis

Supervisors ask why a number moved, not just what it is. Answering takes a governed metric over two windows plus cited evidence from the calls between them. This note, ticket E0, specifies synthetic events with known answers, the `explain_change` route, and seven build tickets (E1–E7, last section). Appendix A holds the schemas. Windows are half-open ISO dates. Weeks are ISO weeks starting Monday.

## Event model

`data/synthetic/events.yml` (A.1) defines four dated events. Each has a population, phases with a volume multiplier per category and a CSAT shift per event call, and a driver mix per category. Event calls are added to the 1,250 baseline calls from a separate seeded stream, using the baseline timing function. All ids are then renumbered in time order, so no draw or id marks an event.

| Event | Split | Date | Population | Volume × by phase | CSAT shift per call | Expected Δ CSAT, default window |
|---|---|---|---|---|---|---|
| Fee schedule change | golden | Wed 2026-04-15 | `fee_dispute`, `account_balance` | fee 8 (6 wk), 3 (to end); balance 1.25 (6 wk) | −1.0, −0.6 | −0.88 (fee_dispute) |
| Card fraud rule change | golden | Mon 2026-06-15 | `card_services`, `fraud_dispute` | 3 (3 wk), 1.5 (5 wk) | −0.9, −0.45 | −0.45 |
| Mobile app version 5 release | heldout | Mon 2026-07-20 | `online_banking_support` | 5 (2 wk), 1.5 (4 wk) | −0.8, −0.4 | −0.45 |
| Westside branch closure | heldout | Fri 2026-08-07 | Westside members in `account_balance`, `account_opening`, `payment_assistance` | 6 (4 wk, cut at data end) | −1.0 | −0.58 (home_branch only), −0.83 (also the three categories) |

Expected Δ = Σ(phase event responses × phase shift) ÷ all after-window responses, before clamping. The ground truth records the realized value, and E1 tests the two agree within 0.15. Members gain `home_branch`. Fee transactions gain `fee_type` and schedule prices: overdraft $29, then $35 from the fee change. With both on the ledger, a data team can tie a fee or branch change to revenue and to calls.

## How drivers are planted and in what proportions

- **Quotas.** Labels go by largest remainder within each event, phase and category, then are shuffled, so planted counts are exact. Every driver, and every pattern as a whole, has at least 6 calls in its default window.
- **Dialogue.** At least three variants per category and per driver. Variants change wording only: never facts, never the agent's name. Account numbers are masked to the last four digits.
- **The ledger matches the calls.** Calls about several overdrafts in one day have 2–4 such fees posted after the change, on the caller's checking account.
- **Coaching.** In 40% of refused-refund calls, the agent misstates the waiver rule in one of three ways. Each such call has a distinct agent, so the pattern spans the team.
- **Hygiene.** Event, driver and pattern ids live only in `events.yml` and `ground_truth/`, never in transcripts, metadata, vectors, OLTP or the semantic layer. `change_log.json` (name and date only) is the one channel by which any event reaches the system.

## Ground-truth file format

`data/synthetic/ground_truth/events.json` (A.2) holds, for each event, planned counts per phase and a `default_window`: filters, windows, realized metrics, and driver and pattern call ids. A `calls` map labels every event call; any call it doesn't list is baseline. E1 adds the folder to `.gitignore` and `.dockerignore`. Evaluators recompute expectations for whatever windows an answer reports, using the semantic layer's time rules: calls by `started_at`, CSAT by `surveyed_at`.

## The `explain_change` route: nodes, state, interrupts

`classify` gains a fourth route. It takes questions about why or whether a measure moved, questions about what members said relative to a change or period, and follow-ups when a stored `change` exists (`has_prior_change` goes into the prompt). No existing eval question uses change or time wording. `agent/subgraphs/change.py` shares parent state and the parent checkpointer, as `analyst` does. State is in A.3.

```
classify → explain_change[frame → resolve_measure ⇄ clarify → resolve_subject → resolve_when → analyze] → ground → answer
            follow-up with a stored change: frame → analyze(mode="coaching")
```

| Node | Behavior |
|---|---|
| `frame` | One structured LLM call. It sees the question and the change log and returns `ChangeFrame` |
| `resolve_measure` | Matches the measure phrase by label, then `change_measures.yml`, then `match_terms`. With no phrase, uses `call_volume` and says so. Refuses anything that isn't exactly one supported metric |
| `clarify` | The analyst's node unchanged, interrupt contract v1, the only interrupt. It fires only when a term leaves several supported metrics, which no current term does, so tests use a fixture term |
| `resolve_subject` | Maps categories via `change_subjects.yml` and branches via `branches.json`. With no subject, keeps the categories whose volume rose (A.5). Refuses unknown subjects |
| `resolve_when` | Uses the logged change, a date, or a period from `parse_period` (A.5). Refuses anything else |
| `analyze` | Calls the MCP tool `explain_change` (A.4). Writes `change`, `tool_output`, `sql`, `metric_names`, `citations` |

`classify` resets every per-run key except `change`, so a follow-up on the same `thread_id` reuses that evidence. `ground` requires both window SQLs and citations from inside the windows, and rejects any agent id. The tool does the work, so MCP clients without interrupts get the same evidence. It runs three MetricFlow queries and counts each window with `Retriever.count(where)`. It reads the window's calls, or 200 of them. One LLM call proposes up to six drivers, and batches of 20 assign each call a driver and a quote. Code does the counting and keeps only verbatim quotes.

## What an answer must contain

`render_change` composes the answer without an LLM. The only model text in it is driver labels and code-checked quotes.

1. The metric, filters, and both windows.
2. Before and after values with `n`, the change, and each window's SQL. Numbers come only from MetricFlow and `count`.
3. How the change point was found: from the question, the change log, or detection with its score. Weekly values for recovery questions. "No clear change point" when nothing qualifies.
4. Each driver as "n of M calls in the after window", or "n of S sampled", with its count before the change and up to three citations.
5. Limits, always including that the timing fits the change but the data can't show the change caused the shift.
6. No per-agent scores, names or ids. Coaching answers name patterns across calls.

A refusal names the phrase that didn't resolve, says what can be explained, and calls no tool.

## Eval plan: golden event types vs held-out event types

- **Split.** Events 1 and 2 are golden. Events 3 and 4 are held out: no golden rows, prompt examples, fixtures or tuning. A.6 fixes all ten questions and their contracts before any code exists. `build_change.py --check` derives every expectation from the ground truth and refuses a golden spec on a held-out event.
- **Scoring.** Deterministic evaluators (A.6) score the change point, the before/after values and the weekly series against recomputed values. They match drivers and patterns to planted calls by call id, never by wording.
- **Generic fixes only.** A fix prompted by a held-out failure must apply unchanged to golden events and be justified by golden or unit evidence. A failing held-out demo step is reported, not patched.
- **Keyword guard.** A test fails if held-out driver keywords appear in prompts, agent data, `change_log.json` or golden datasets.
- **Measurement.** Offline doubles script the classifier, so they can't measure a prompt change. The owner runs `make evals-live` before and after E3's classify change and compares `route` scores. Results report `change_golden` and `change_heldout` separately.

## Design decisions and their reasons

1. **Apr 15 for the fee change** leaves a full 42-day before window, since the data starts Mar 3.
2. **Large multipliers on small categories.** Fee disputes run about 1.4 calls a week, so a ×3 spike would give a 10% driver about 2 calls. These multipliers meet the 6-call floor.
3. **Quotas make tests exact.** A ±5-point tolerance applies only to shares inside a window.
4. **Additive events with renumbered ids.** Baseline values hold, and no id marks an event. The corpus grows to about 1,550 calls (A.8 lists which 1,250 references move).
5. **Fine-tuning stays on baseline calls.** A model trained on event calls would learn the planted drivers.
6. **Structured metric filters** (`eq`/`in` on named dimensions) instead of a raw MetricFlow string. They validate and render the same on all three warehouses.
7. **The core lives in the tool**, so every MCP client gets the graph's evidence.
8. **The change log has names and dates only**, which resolves "after the fee schedule change" without exposing a cause.
9. **The model labels drivers and code counts them**, and scoring uses call-id overlap.
10. **The `fee` term lists `total_fee_revenue` and `unless` phrases** (A.7), so existing fee questions don't start interrupting.
11. **Coaching is a tool mode**, not a seventh tool.

## Follow-on tickets E1 to E7

| Ticket | Branch | Deps | Builds |
|---|---|---|---|
| E1 Event-driven synthetic data | `t/e1-event-synthetic-data` | E0 | Events, variants, ledger facts, ground truth, change log, `home_branch` filtering on both retrievers, corpus-size updates |
| E2 Metric windows, fee metrics, `fee` term | `t/e2-metric-windows-fee-terms` | E0; data checks after E1 | Windowed, filtered `query_metric`; dated `query_csat`; four fee metrics; the `fee` term |
| E3 `explain_change` route and tool | `t/e3-explain-change-route` | E1, E2 | `ccai_mcp/change.py`, the sixth tool, the agent route |
| E4 `explain_change` for MCP clients | `t/e4-explain-change-tool` | E3 | Client description, README, `call_tool` test. Uses the existing `http_smoke --call` |
| E5 Change-analysis evals | `t/e5-change-evals` | E1, E3 | Datasets, evaluators, doubles, result keys |
| E6 Change demo and preflight | `t/e6-change-demo` | E3; E4 for step 6 | `make demo-change`, six timed steps: fee-call CSAT; "What do we make on fees?" (clarify); coaching follow-up; online banking; open a citation; step 1 over MCP. A preflight prints the fix for each missing piece |
| E7 Audio per turn (stretch) | `t/e7-turn-audio` | E1; E3 citations | Local TTS per turn (license checked first) and `audio_index.json`. LanceDB stores audio, pgvector stores a path |

E1 and E2 run in parallel. A.8 assigns every file and orders shared ones.

---

## Appendix A: interfaces

### A.1 `data/synthetic/events.yml`

```yaml
version: 1
branches: {Westside: 0.20, Downtown: 0.30, Northgate: 0.25, Riverside: 0.25}
events:
  - id: fee_schedule_change
    split: golden                                  # golden | heldout
    date: 2026-04-15
    change_log_name: Fee schedule change           # the only text that leaves the generator
    population: {category: [fee_dispute, account_balance], home_branch: null}
    phases:
      - {weeks: 6, volume: {fee_dispute: 8.0, account_balance: 1.25}, csat_shift: -1.0}
      - {weeks: end, volume: {fee_dispute: 3.0}, csat_shift: -0.6}
    drivers:                                       # share per category; keywords feed the isolation test
      - {id: unaware_of_change, share: {fee_dispute: 0.45, account_balance: 0.60}, fee_type: overdraft, keywords: [...]}
      - {id: courtesy_refund_refused, share: {fee_dispute: 0.25}, fee_type: overdraft,
         patterns: [{id: inconsistent_waiver_explanation, share: 0.40, variants: [once_a_year, once_ever, partial_refund]}]}
      - {id: multiple_overdrafts_same_day, share: {fee_dispute: 0.20, account_balance: 0.40}, fee_type: overdraft,
         ledger: {overdraft_fees: [2, 4]}}
      - {id: other_fee_question, share: {fee_dispute: 0.10}, fee_type: {nsf: 0.4, monthly_maintenance: 0.4, atm: 0.2}}
```

The drivers of the other three events:

- **Card fraud rule change:** new-merchant decline 50, verification text missing or unclear 30, subscription decline 20, in both categories.
- **Mobile app version 5 release:** biometric re-enrollment 55, passcode not received 30, bill-pay payees missing 15.
- **Westside branch closure:** cash or coin deposits 40, safe deposit box 35, notary or cashier's check 25, in all three categories.

`branches.json` = `[{"name"}]`, and `change_log.json` = `[{"name", "date"}]`.

**Generation rules (E1):**

- **Random streams.** Event calls come from their own `random.Random`, seeded from 42, and dialogue variants come from a third. The baseline stream keeps its draws.
- **Volume.** For each phase and category, the number of event calls is `round((multiplier − 1) × baseline weekly rate × weeks)`. The rate is measured on the generated baseline population.
- **Timing.** Dates come from the baseline timing function, weekend-to-Friday roll included. Every phase starts on a weekday, so a rolled date stays inside its phase.
- **Ids.** After the merge, interaction, survey and transaction ids are reassigned in `(timestamp, sha256("member_id|category|timestamp"))` order.
- **CSAT.** An event call is scored like a baseline call, then lowered by `csat_shift`: always the integer part, and one more point with probability equal to the fraction, clamped to 1–5. A test checks that the mean per-call shift is within 0.1 of `csat_shift`.
- **`home_branch`.** Assigned from SHA-256(`member_id`) against the `branches` shares. This is not Python's `hash()`, which is salted per process.
- **Fee prices.** Overdraft is $29, then $35 from the fee change. NSF is $29 and ATM is $3. Monthly maintenance is the account's fee, or $5 when that fee is zero. Non-banking lines get `other`. Banking fees are split by hash: overdraft 40%, maintenance 35%, NSF 15%, ATM 10%. The baseline amount is still drawn and then replaced, so the stream stays aligned.
- **Same-day overdrafts.** Callers for `multiple_overdrafts_same_day` are drawn from members who have a checking account. Their 2–4 fees post on one day between the event date and the call.
- **Event 1 balance calls.** `account_balance` event calls in event 1 carry only the two balance-related drivers.

### A.2 `data/synthetic/ground_truth/events.json` (values show the shape only)

```json
{"version": 1, "seed": 42, "events_yml_sha256": "…", "data_range": {"start": "2026-03-03", "end": "2026-09-02"},
 "events": [{"id": "fee_schedule_change", "split": "golden", "date": "2026-04-15",
   "phases": [{"start": "2026-04-15", "end": "2026-05-27", "planned": {"fee_dispute": {"unaware_of_change": 27}}}],
   "default_window": {"filters": {"category": ["fee_dispute"]},
     "before": {"start": "2026-03-04", "end": "2026-04-15"}, "after": {"start": "2026-04-15", "end": "2026-05-27"},
     "metrics": {"call_volume": {"before": 0, "after": 0}, "csat_average_score": {"before": 0.0, "after": 0.0},
                 "responses": {"before": 0, "after": 0}, "csat_shift_realized": 0.0},
     "drivers": [{"id": "unaware_of_change", "before": [], "after": ["CALL-…"]}],
     "patterns": [{"id": "inconsistent_waiver_explanation", "driver": "courtesy_refund_refused",
                   "variants": {"once_a_year": ["CALL-…"], "once_ever": [], "partial_refund": []}}]}}],
 "calls": {"CALL-…": {"event": "fee_schedule_change", "phase": 0, "driver": "unaware_of_change", "pattern": null, "variant": null}}}
```

### A.3 State

```python
Route = Literal["retrieve", "resolve_metric", "summarize_call", "explain_change"]
class ChangeFrame(TypedDict):
    measure: str | None          # "satisfaction"
    subject: str | None          # "fee calls"
    when: str | None             # "late July", "since April", "2026-06-15"
    logged_change: str | None    # a change_log name, chosen by frame
    follow_up: bool
# AgentState += change_frame, change_filters: dict, change_point: str | None,
#                change_search: dict | None ({"start", "end"}), change: dict | None
# AgentOutput += change: dict | None
```

### A.4 Tool `explain_change` (`ccai_mcp/change.py`)

```python
def explain_change(metric: str, filters: dict | None = None, change_point: str | None = None,
                   search_range: dict | None = None, window_days: int = 42, question: str | None = None,
                   mode: Literal["drivers", "coaching"] = "drivers", groups: list[dict] | None = None) -> dict
def render_change(result: dict) -> str
def split_change(tool_text: str) -> tuple[str, dict]       # used by analyze
```

**Inputs.**

- The input schema mirrors the Python signature.
- `filters` is `{"category": [...], "home_branch": [...]}`.
- `search_range` is `{"start", "end"}`.
- `window_days` runs from 14 to 91.
- `groups` is `[{"label", "call_ids"}]`, coaching mode only.

**Supported metrics:**

- `call_volume`: a count, compared per week.
- `csat_average_score`: a mean, weighted by `responses`, σ taken as 1.0.
- `first_contact_resolution_rate`: a ratio over `call_volume`, binomial.
- `average_handle_time`: a ratio over `call_volume`. It needs an explicit change point.

Any other metric returns `TOOL ERROR` with a message that lists these.

**Wire format.** The result has two `TextContent` blocks:

1. `render_change(result)`, the cited answer that `http_smoke --call` and Claude Desktop show first.
2. A line `CHANGE RESULT (JSON, v1):` followed by `json.dumps(result)`.

`MCPToolbox` joins the blocks with a newline. `analyze` calls `split_change` on that marker, the same way `split_sql` splits on `Generated SQL:`.

```json
{"version": 1, "mode": "drivers", "metric": "csat_average_score", "filters": {"category": ["fee_dispute"]},
 "change_point": {"date": "2026-04-15", "source": "argument|detected|none", "logged_change": "Fee schedule change",
                  "score": null, "direction": null, "reason": null},
 "windows": {"before": {"start": "…", "end": "…", "value": 0.0, "n": 0, "sql": "…"},
             "after": {"start": "…", "end": "…", "value": 0.0, "n": 0, "sql": "…",
                       "weekly": [{"week": "2026-04-13", "value": 0.0, "n": 0}]}},
 "population": {"before": 0, "after": 0}, "coverage": {"read_before": 0, "read_after": 0},
 "drivers": [{"label": "…", "definition": "…", "before": {"n": 0, "call_ids": []}, "after": {"n": 0, "call_ids": []},
              "direction": "up|down|flat",
              "citations": [{"call_id": "CALL-…", "quote": "verbatim transcript text"}]}],
 "patterns": [], "unassigned": {"before": 0, "after": 0}, "limits": ["…"]}
```

### A.5 Periods, detection, windows

**Period grammar** (`agent/changes.parse_period(phrase, data_range) -> {"start", "end"} | None`):

- An ISO date or "Month D" is a point.
- "Month", "in", "during", "since" or "after Month" means that month.
- "early", "mid" or "late Month" means days 1–10, 11–20, or 21 to the end.

The year is the one in which that month falls inside the data range. A period is padded by 3 days on each side. Anything else is refused.

**A stated point snaps to a logged change.** When the question gives a single date and a logged change falls within 3 days of it (`POINT_SNAP_DAYS`, the same distance as the period padding), the logged change's date is the change point. The nearest wins, the earlier on a tie. The answer opens with a note that says which date was given, which logged change it was moved to, and the tolerance. A date with no logged change within 3 days is used as stated.

**Resolving the change point:**

1. If the padded period holds exactly one logged change, its date is the change point (`source: argument`, as for a stated date).
2. Otherwise the tool detects within the padded period, or across the full range when there is no period.

**Detection.**

1. **Candidates.** Each Monday *t* is a candidate. The before side is [*t* − min(13 wk, available), *t*), and the after side is [*t*, *t* + min(4 wk, available)).
2. **Values.** Counts are compared as calls per week. Means and ratios are pooled: Σ(value × weight) ÷ Σweight.
3. **Minimum.** A side needs at least 8 records.
4. **Score.** It is a standardized shift. For counts: |r_a − r_b| ÷ √(r_a/w_a + r_b/w_b), with r the weekly rate and w the number of weeks. For means and ratios: |Δ| ÷ (σ·√(1/n_a + 1/n_b)).
5. **Choice.** The highest score wins; on a tie, the earliest. A score below 2 gives `source: none`, with a reason, no drivers, and a request for a date.
6. **Snap.** A logged change within 7 days replaces *t*.
7. **Direction.** Candidates are scored in either direction, and the winner's direction is reported in `change_point.direction` (`rise` or `fall`: after against before, in calls a week for counts and in the pooled value otherwise) and in the answer text. It is `null` when the change point was given, because the windows' values show the movement.

**Detection alone is not evidence.** When the change point was detected (not given) and the calls after it hold no driver, the answer opens with "Not enough evidence", gives no cause, and `limits` starts with the same sentence. Weekly noise in a call type nothing happened to can pass the score threshold; the calls are the check. `ground` treats such an answer as claiming nothing, so it needs no citation.

**Driver direction.** Each driver's `direction` compares its calls in the two windows, scaled to the window populations when a window was sampled: `up` when more calls after the change, `down` when fewer, `flat` when equal. The answer says "more than before", "fewer than before" or "about the same as before" beside the before count.

**Windows.** They are [cp − L, cp) and [cp, cp + L), with L = min(`window_days`, the days available on each side).

**Subjects.** When no subject is given, categories are kept by the same count score, from one `call_volume` query grouped by `interaction__category_code` per window.

**Sampling.** Above 200 calls in a window, the tool reads the 200 lowest SHA-256(call_id) values. Shares are n ÷ sampled, and `limits` says so.

### A.6 Question contract

The default filters come from `change_subjects.yml`: "fee calls" and "fees" map to `fee_dispute`; "card calls" and "declined cards" map to `card_services` and `fraud_dispute`.

| # | Question | Metric | Filters | Change point (source) | Interrupt | Evaluators beyond `route`, `metric`, `sql`, `citations`, `no_agent_ids` |
|---|---|---|---|---|---|---|
| G1 | Why did satisfaction on fee calls drop after the fee schedule change? | `csat_average_score` | fee_dispute | Apr 15 (logged) | no | change_point, before_after, driver_recall, driver_share |
| G2 | What are members saying about fees since April? | `call_volume` (default) | fee_dispute | Apr 15 (period → one logged) | no | same as G1 |
| G3 | What should supervisors coach on for fee calls? (follow-up to G1) | from G1 | from G1 | from G1 | no | pattern_recall |
| G4 | Why did card calls spike in June? | `call_volume` | card_services, fraud_dispute | Jun 15 (period → one logged) | no | same as G1 |
| G5 | What are members upset about with declined cards since the fraud rule change? | `call_volume` (default) | card_services, fraud_dispute | Jun 15 (logged) | no | same as G1 |
| G6 | Did satisfaction on card calls recover after the fraud rule change? | `csat_average_score` | card_services, fraud_dispute | Jun 15 (logged) | no | change_point, before_after, weekly, driver_recall |
| H1 | Why did online banking calls go up in late July? | `call_volume` | online_banking_support | Jul 20 (period → one logged) | no | same as G1 |
| H2 | What changed for members after the app update? | `call_volume` (default) | categories that moved | Jul 20 (logged) | no | same as G1 |
| H3 | Why is satisfaction down for Westside members? | `csat_average_score` | home_branch Westside | detected, snaps to Aug 7 | no | same as G1 |
| H4 | What do members need since the Westside branch closed? | `call_volume` (default) | home_branch Westside | Aug 7 (logged) | no | same as G1 |

Row shape: `{"id", "kind": "change", "event", "question", "follow_up_of", "route", "metric_names", "filters", "change_point", "change_point_source", "expect_interrupt", "evaluators", "reference", "derivation"}`.

The golden questions live in `evals/datasets/change_specs.py` and the held-out ones in `change_holdout_specs.py`, each copied verbatim from the table. E2 also adds one ambiguous row to the existing golden set: "What do we make on fees?".

**Evaluators** (`evals/evaluators.py`) are recomputed for the windows each output reports:

- `change_point` must match exactly when the source is the change log or the question. A detected point may be off by up to 4 days, which is less than the 7-day snap radius.
- `before_after` and `weekly` must be equal at two decimals.
- `driver_recall`: a planted driver with at least 5 calls in scope is found when a single reported driver holds at least 50% of its calls, at 60% precision or better.
- `driver_share` must be within 10 points.
- `pattern_recall` applies the `driver_recall` rule to pattern calls.
- `no_agent_ids` rejects any agent id.

These run alongside `route`, `metric`, `sql`, `citations` and the live judge.

The isolation test also scans the prompt strings in `ccai_mcp/change.py`. E5 extends `ScriptedClassifier` to script `frame`. Results keep the existing per-group `metrics` keys, so `tools/results.py` renders them unchanged.

### A.7 Metric inputs (E2)

**`query_metric`:**

- Signature: `query_metric(metrics, group_by=None, decimals=None, limit=None, time_window=None, where=None)`. `query_metric_raw(metric_names, group_by=None, limit=None, *, time_window=None, where=None)` keeps its return keys.
- `time_window` is `{"start", "end"}`. MetricFlow's `--end-time` is inclusive, so E2 passes end − 1 day.
- `where` is `[{"dimension": "interaction__category_code" | "member__home_branch" | "transaction__fee_type", "op": "eq" | "in", "value": …}]`. Dimension names are validated and values quoted.

**`query_csat`:**

- Signature: `query_csat(min_score=None, max_score=None, category=None, start_date=None, end_date=None)`, filtering on `surveyed_at`.
- A parity test checks it against the `responses` metric over the same window.

**Fee metrics** (`metrics.yml`, 43 → 47): `overdraft_fee_revenue`, `nsf_fee_revenue`, `monthly_maintenance_fee_revenue` and `atm_fee_revenue`, each filtered on `transaction__fee_type`.

**`fee` term:**

- candidates: those four plus `total_fee_revenue`
- `unless`: [fee call, fee calls, fee dispute, fee disputes, dispute fees, fee schedule, fee change]

### A.8 File ownership and shared files

**E1:**

- `data/synthetic/events.yml`, `generate_data.py`, `ground_truth/`.
- `olap/oltp/schema.sql` (`member.home_branch`, `account_transaction.fee_type`), `olap/seed.py`.
- `rag/pipeline.py` (`home_branch` metadata).
- `retrieval/base.py` (`home_branch` in `where`), `pgvector_backend.py` (`count` takes the full portable `where`, as `search` does), `lancedb_backend.py` (a `home_branch` column; existing `data/lance` tables need `--reset`), and one parity test of `count` and `search` on both backends.
- `models/finetune/gold.py` (match any variant; baseline calls only), `models/finetune/splits.py`, `retrieval/labels/build_labels.py` (match any variant), rebuilt `queries.jsonl`, `agent_golden.jsonl` and `agent_holdout.jsonl`.
- `.gitignore`, `.dockerignore`, `tests/data/`.

**E2:**

- `ccai_mcp/metrics.py`, `query_csat` in `ccai_mcp/tools.py`, and those two schemas in `ccai_mcp/server.py`.
- `olap/dbt/`, `metrics.yml`, `ambiguous_terms.yml`, the ambiguous row in `build_golden.py`.
- The Ossie export and `docs/semantics/README.md` (rerun the converter for `test_ossie_export.py`).
- The additive exception in `docs/AGENT_HANDOFF.md` §3: tools may gain optional inputs, and a sixth tool is added. Existing calls behave as before.

**E3:**

- `ccai_mcp/change.py`, the sixth tool in `server.py`.
- `tests/test_server_handshake.py` (six names), `tests/test_server_http.py`, `tests/agent/`.
- `agent/state.py`, `graph.py`, `tools.py` (docstring), `nodes/classify.py`, `nodes/ground.py`, `prompts/`, `subgraphs/change.py`, `changes.py`, `data/change_*.yml`.
- `agent/README.md`, `docs/design/agent-graph.md`, `ccai_mcp/README.md` (the tool count).

**E4:** the tool description and `ccai_mcp/README.md` (client usage).

**E5:**

- `evals/datasets/change_*`, `evaluators.py`, `offline.py`, `run.py` (`--dataset change`, `change-holdout`).
- `known_failures.json`, `evals/README.md`.

**E6:** the `Makefile` target, `agent/demo_change.py`, `tools/demo_chat.py`, and the regenerated `docs/demo-chat.md`.

**Shared files, edited in dependency order:**

- `tests/fixtures/mcp_tool_schemas.json` is regenerated by E2, then E3, then E4.
- `ccai_mcp/server.py`: E2, E3, E4.
- `CLAUDE.md` and `docs/TOUR.md`: the call count (E1), the metric count (E2), the tool count and route (E3).
- `Makefile`: E1 (comment), E6.

**The 1,250 references.**

- These move to the new total: `tests/test_published_commands.py` `EXPECTED_TRANSCRIPTS`, `CLAUDE.md`, `README.md`, the current-state counts in `docs/TOUR.md`, `Makefile:28`, and the docstrings in `retrieval/bench.py`, `lancedb_backend.py` and `duckdb_lance.py`. All are E1's.
- These stay: the fine-tune corpus (`gold.py`, `splits.py` and their tests), which keeps the 1,250 baseline calls; test fixtures with literal values (`test_parity.py`, `test_retrieval.py`, `test_demo_chat.py`, `test_export.py`); dated `results/` records; and published posts.
