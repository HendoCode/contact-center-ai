# Fine-tune dataset (F1)

Builds the `summary_v1` dataset for the fine-tune-or-retrieve experiment. Task, splits and
fairness rules are in [`docs/design/finetune.md`](../../docs/design/finetune.md).

| File | What it does |
|---|---|
| `schema.py` | `summary_v1` JSON Schema, enum definitions, and `validate_summary()` (schema plus "each metric value occurs verbatim in the text") |
| `gold_rules.yml` | per-category rules for `product_line`, `metric_mentioned`, `resolution`, `follow_up` |
| `gold.py` | builds gold labels from generator output plus the rules; no model involved |
| `splits.py` | 800 / 150 / 300 split by `sha256("summary_v1:" + call_id)`, stratified by category |
| `prompts.py` | the system prompt, shared by the teacher and (later) the experiment arms |
| `data_gen.py` | CLI: `build`, `label`, `finalize` |
| `claude_code.py` | `LLM_PROVIDER=claude-code`: the teacher through `claude -p` on a Claude subscription, no API key |

```bash
python data/synthetic/generate_data.py             # once, if data/synthetic/*.json is missing
make finetune-data                                 # offline: gold.jsonl, splits.json, test.jsonl, manifest.json
make finetune-label ARGS="--dry-run"               # how many calls are still unlabeled; no API call
LLM_PROVIDER=anthropic ANTHROPIC_MODEL=claude-sonnet-5-5 \
  make finetune-label ARGS="--limit 5"             # smoke test: 5 calls, spends API money
LLM_PROVIDER=anthropic ANTHROPIC_MODEL=claude-sonnet-5-5 \
  make finetune-label                              # teacher labels for train + dev (950 calls); resumable
```

Without an API key, `LLM_PROVIDER=claude-code` sends the same prompt through the Claude Code
CLI on a logged-in Claude subscription (same validation, retry and resume). The model is
`CLAUDE_CODE_MODEL` (default `sonnet`). Each call runs lean, with this prompt in place of
Claude Code's and no tools, so it costs about 1,400 input and 100 output tokens of the
subscription's usage; the full 950 calls are roughly 1.4M tokens. Lower `--workers` if the
subscription rate-limits.

```bash
LLM_PROVIDER=claude-code make finetune-label ARGS="--limit 5"   # smoke test
LLM_PROVIDER=claude-code make finetune-label
```

Output goes to `data/finetune/` (gitignored):

- `gold.jsonl`: gold label for all 1,250 calls, with the split each belongs to.
- `splits.json`: `call_id`s per split.
- `teacher_labels.jsonl`: one validated teacher label per `call_id`. Rerunning `label` skips
  finished calls and drops stale or duplicate rows.
- `train.jsonl`, `dev.jsonl`: text, teacher `label` (the training target) and `gold`.
- `test.jsonl`: text and `gold` only.
- `review_sample.jsonl`: 50 teacher-labeled calls spread across all 14 categories, each with the
  teacher label, the gold label and per-field agreement. Fill in the `review` block by hand.
- `teacher_failures.jsonl`: calls that exhausted their retries (retried on the next run).
- `manifest.json`: counts per split and category, file hashes, `dataset_sha256`, and
  teacher-vs-gold agreement.
