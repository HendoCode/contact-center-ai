# Fine-tune or retrieve (W1)

D0.1, 2026-10-02. Feeds F1–F6. Checked against TRL 1.14.1, PEFT 0.21.2, vLLM 0.30.0, Fireworks' tunable-model list (2026-09-30), and generator output at `6775cb4`.

**Corpus limits.**
- Once names and figures are masked, the 1,250 transcripts reduce to fewer than 30 distinct texts (one to five per category). In-template scores will therefore sit near the ceiling.
- So this experiment measures format adherence, value extraction, latency and cost. It does not measure comprehension, and B1 says so.
- `outcome` never appears in the text, so no label uses it.
- Any claim about generalization rests on test-para (F1b).

## Task and labels

`summary_v1` is a strict JSON Schema with every key required. Gold labels come from generator output plus `gold_rules.yml`, never from a model:
- `reason_for_call` (14 categories): from `category`.
- `product_line` (6 LOBs or `unknown`): from the subject account's LOB. `account_opening` maps to `banking`. `rate_lock_status` maps to `unknown`, because its text carries no LOB cue.
- `metric_mentioned`, a list of `{name, value}`: every account figure spoken in the call. `name` is a generator fact key. `value` is verbatim, and F1 asserts it occurs in `full_text`.
- `resolution` (6-value enum) and `follow_up` (`{needed, action}`): per-category rules.

**Training targets.** Training labels come from a teacher model, `claude-sonnet-5-5`. It labels train and dev through F1's validate/retry/resume loop, mirroring production, where no metadata exists to label from. The manifest reports teacher-vs-gold agreement. Rejected: teacher labels as gold, which would grade the frontier arm against itself.

**Splits.** 800 train, 150 dev, 300 test, stratified by category and ordered by `sha256("summary_v1:" + call_id)`. At 90% accuracy, 300 test rows give a 95% CI of ±3.4 points, versus ±5.9 at F1's 100.

**test-para.** An OpenAI-family model paraphrases the test calls. A rewrite that drops any gold value is retried.

## Model and training

**Base model:** `Qwen/Qwen3-4B-Instruct-2507`.
- Apache-2.0 and ungated.
- Fireworks can tune it as `accounts/fireworks/models/qwen3-4b-instruct-2507`, so both fine-tuned arms share the same weights.
- About 9 GB in bf16, so plain LoRA fits a 24 GB A10.
- No thinking mode.

Rejected:
- Llama 3.x: gated, and the 8B forces QLoRA.
- Qwen3-8B: thinking mode.
- Qwen3.5-9B: about 19 GB of weights leaves too little KV cache.

**F2 training (TRL + PEFT).**
- Format: prompt–completion, so loss covers only the completion. The system prompt matches the prompted arms.
- LoRA: r 16, alpha 32, dropout 0.05 on `all-linear`. Per QLoRA, covering every layer matters more than rank. r 16 fits Fireworks' power-of-two ≤ 32 limit, so F3 can match it.
- Schedule: LR 1e-4 (TRL's guidance for adapters), cosine, 5% warmup, batch 16, 2 epochs. Keep the checkpoint with the best dev loss.
- `max_length` 1024, failing on any truncation. bf16. Seed 42.

Rejected: Unsloth. It gives no gain at about 100 steps, and the dry run would diverge from the real run. `--dry-run` trains a tiny random Qwen3 on CPU, offline.

## Arms

| Arm | Model | Extra input | Served by |
|---|---|---|---|
| A0 base | Qwen3-4B-Instruct-2507 | none | vLLM on A10 |
| A1 retrieve | same | 4 nearest train calls with labels | vLLM on A10 |
| A2a fine-tuned | same + F2 adapter | none | vLLM on A10 |
| A2b fine-tuned | same, F3 LoRA | none | Fireworks |
| A3 frontier | `claude-sonnet-5-5` | none | Anthropic |

A1 retrieves with `get_retriever().search(text, k=4, where={"call_id": {"in": train_ids}})`. A1 and A2 get the same supervision. That is four arms, with the fine-tuned arm served two ways; the five rows are F6's arms.

## Metrics and fairness

**Metrics.**
- Validity: an output that fails the schema scores zero on every field.
- Accuracy: exact match on enums and `needed`, pair-level F1 on `metric_mentioned`.
- Judge: `google/gemini-3.8-flash` via OpenRouter, prompt `judge_v1`, temperature 0. It scores `action` equivalence and faithfulness. If it scores the gold labels below 4.5, fix the judge first.
- Latency and cost: p50/p95 latency and throughput at concurrency 16. Cost is tokens × list price for APIs and GPU $/hour ÷ throughput for self-hosted arms. Training cost is reported separately.
- Uncertainty: paired 95% bootstrap CIs over `call_id`. test and test-para are reported separately.

**What counts as a fair comparison.**
1. Same 300 IDs, text, prompt and schema for every arm. The test-file hash goes in each results JSON.
2. Temperature 0, `max_tokens` 512, one attempt, no JSON mode.
3. A0–A2b share weights. A2a and A2b also share data, rank and epochs.
4. Tune on dev only. Run each model version on test once.
5. A1 sees train only. Gold labels, the judge and the paraphraser never come from an arm's model family. A skipped arm is reported as "not run."

## Tickets

- **F1 · Dataset** (refined) · S. Gold builder, teacher labels, splits, manifest. **Stephen runs:** labeling.
- **F1b · Paraphrased test set** (**new**) · S · deps F1. **Stephen runs:** generation and a 20-row hand check.
- **F2 · LoRA SFT** (refined). Settings above. The model card records the data hash.
- **F3 · Fireworks** (refined). Rank 16, 2 epochs. Delete the deployment after F6, because it bills while it is up.
- **F4 · vLLM** (refined). Serve with `--enable-lora --lora-modules summary-v1=<path> --max-lora-rank 16`, so A0, A1 and A2a share one server. The quantized variant is AWQ W4A16, because Ampere has no FP8 tensor cores.
- **F5.** Add a concurrency-16 point.
- **F6 · Evals** (refined) · deps F1, F1b, R1, F2–F4. `get_llm` gains an optional `model` override.
