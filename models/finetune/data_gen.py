"""
F1 dataset builder for the summary_v1 fine-tune-or-retrieve experiment.

Three steps, all writing under `data/finetune/` (gitignored):

  build     offline, no API. Gold labels for all 1,250 calls (code + gold_rules.yml),
            the 800/150/300 split, test.jsonl and a manifest.
  label     the teacher (`get_llm`, e.g. claude-sonnet-5-5) labels train and dev
            through a validate / retry / resume loop. Costs API money.
  finalize  joins teacher labels onto train/dev, writes review_sample.jsonl and
            refreshes the manifest. `label` runs it at the end.

Every record is keyed by `call_id`. `label` skips calls that already have a valid
teacher label, so an interrupted or repeated run never duplicates or re-spends.

    python -m models.finetune.data_gen build
    LLM_PROVIDER=anthropic ANTHROPIC_MODEL=claude-sonnet-5-5 \
        python -m models.finetune.data_gen label
    LLM_PROVIDER=claude-code CLAUDE_CODE_MODEL=sonnet \
        python -m models.finetune.data_gen label      # a Claude subscription, no API key
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from models.finetune.gold import (
    DEFAULT_DATA_DIR,
    GOLD_RULES_PATH,
    REPO_ROOT,
    CallRecord,
    build_all_gold,
    load_calls,
    load_dialogue_templates,
    load_rules,
)
from models.finetune.prompts import SYSTEM_PROMPT, user_prompt
from models.finetune.schema import SCHEMA_VERSION, schema_sha256, validate_summary
from models.finetune.splits import SPLIT_NAMES, make_splits

DEFAULT_OUT_DIR = REPO_ROOT / "data" / "finetune"
TEACHER_SPLITS = ("train", "dev")
REVIEW_SAMPLE_SIZE = 50
REVIEW_MIN_PER_CATEGORY = 2
MAX_ATTEMPTS = 3
MAX_CONSECUTIVE_FAILURES = 10

GOLD = "gold.jsonl"
SPLITS = "splits.json"
TEACHER = "teacher_labels.jsonl"
FAILURES = "teacher_failures.jsonl"
REVIEW = "review_sample.jsonl"
MANIFEST = "manifest.json"
SPLIT_FILES = {name: f"{name}.jsonl" for name in SPLIT_NAMES}


# ── small IO helpers ──────────────────────────────────────────────────────────


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file and rename, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    _write_atomic(path, "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── build: gold labels + splits ───────────────────────────────────────────────


def build(data_dir: Path = DEFAULT_DATA_DIR, out_dir: Path = DEFAULT_OUT_DIR) -> dict:
    """Write gold.jsonl, splits.json, test.jsonl and the manifest. Offline and deterministic."""
    calls = load_calls(data_dir)
    gold = build_all_gold(calls, load_rules(), load_dialogue_templates())
    splits = make_splits([(c.call_id, c.category) for c in calls])
    split_of = {cid: name for name, ids in splits.items() for cid in ids}

    by_id = {c.call_id: c for c in calls}
    write_jsonl(
        out_dir / GOLD,
        [
            {"call_id": cid, "category": by_id[cid].category, "split": split_of[cid], "text": by_id[cid].text, "gold": gold[cid]}
            for cid in sorted(by_id)
        ],
    )
    _write_atomic(out_dir / SPLITS, json.dumps(splits, indent=1) + "\n")
    write_jsonl(out_dir / SPLIT_FILES["test"], [_example(by_id[cid], gold[cid]) for cid in splits["test"]])
    return write_manifest(out_dir, source_sha256=sha256_file(data_dir / "transcripts.json"))


def _example(call: CallRecord | dict, gold: dict, teacher: dict | None = None) -> dict:
    """One dataset row: the text, the gold label and (train/dev) the teacher label."""
    if isinstance(call, CallRecord):
        call = {"call_id": call.call_id, "category": call.category, "text": call.text}
    row = {"call_id": call["call_id"], "category": call["category"], "text": call["text"], "gold": gold}
    if teacher is not None:
        row["label"] = teacher
    return row


# ── teacher labeling ──────────────────────────────────────────────────────────


class LabelError(Exception):
    """The teacher's reply could not be turned into a valid summary_v1 label."""


def _reply_text(response: Any) -> str:
    """Plain text of a chat reply; Anthropic replies may be a list of content blocks."""
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def parse_label(reply: str, full_text: str) -> dict:
    """Parse a reply into a validated label, or raise LabelError listing every problem."""
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        raise LabelError("reply contains no JSON object")
    try:
        label = json.loads(reply[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LabelError(f"reply is not valid JSON: {exc}") from exc
    errors = validate_summary(label, full_text)
    if errors:
        raise LabelError("; ".join(errors))
    return label


def label_one(
    llm: Any,
    text: str,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict, int]:
    """Ask the teacher for a label, retrying on API errors and on invalid replies.

    A rejected reply goes back to the model with the validation errors. Returns
    the label and the number of attempts used; raises LabelError when attempts run out.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    messages = [SystemMessage(SYSTEM_PROMPT), HumanMessage(user_prompt(text))]
    problem = "no attempt made"
    for attempt in range(1, max_attempts + 1):
        try:
            reply = _reply_text(llm.invoke(messages))
        except Exception as exc:  # network, rate limit, provider outage
            problem = f"API error: {type(exc).__name__}: {exc}"
            sleep(2**attempt)
            continue
        try:
            return parse_label(reply, text), attempt
        except LabelError as exc:
            problem = str(exc)
            messages += [
                AIMessage(reply),
                HumanMessage(f"That reply was rejected: {problem}. Reply with the corrected JSON object only."),
            ]
    raise LabelError(f"{max_attempts} attempts failed; last: {problem}")


def load_teacher_labels(out_dir: Path, texts: dict[str, str], *, compact: bool = False) -> dict[str, dict]:
    """Valid teacher records by call_id. With `compact`, rewrite the file without duplicate or stale rows.

    A row is stale when its text hash no longer matches the call or its label
    no longer validates (for instance after a schema change); such calls are
    labeled again.
    """
    path = out_dir / TEACHER
    rows = read_jsonl(path)
    kept: dict[str, dict] = {}
    for row in rows:
        cid = row.get("call_id")
        text = texts.get(cid)
        if text is None or row.get("text_sha256") != sha256_text(text):
            continue
        if validate_summary(row.get("label"), text):
            continue
        kept.setdefault(cid, row)
    if compact and len(kept) != len(rows):
        write_jsonl(path, list(kept.values()))
    return kept


def teacher_llm(provider: str | None = None) -> Any:
    """The teacher for `provider` (default LLM_PROVIDER): `claude-code` runs `claude -p`,
    anything else is `rag.pipeline.get_llm`."""
    if (provider or os.getenv("LLM_PROVIDER", "")).lower() == "claude-code":
        from models.finetune.claude_code import ClaudeCodeTeacher

        return ClaudeCodeTeacher()
    from rag.pipeline import get_llm

    return get_llm(provider)


def _model_name(llm: Any) -> str:
    return str(getattr(llm, "model", None) or getattr(llm, "model_name", None) or "unknown")


def label(
    out_dir: Path = DEFAULT_OUT_DIR,
    llm: Any = None,
    *,
    provider: str | None = None,
    splits: tuple[str, ...] = TEACHER_SPLITS,
    limit: int | None = None,
    workers: int = 4,
    max_attempts: int = MAX_ATTEMPTS,
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict:
    """Teacher-label the pending calls in `splits`, appending each result to teacher_labels.jsonl.

    Resumable: calls with a valid label on disk are skipped. A call that exhausts its
    attempts is recorded in teacher_failures.jsonl and retried on the next run. The run
    stops early after `max_consecutive_failures` failures in a row (a bad key, an outage).
    """
    if not (out_dir / SPLITS).exists():
        raise FileNotFoundError(f"{out_dir / SPLITS} not found; run `build` first")
    if llm is None:
        llm = teacher_llm(provider)
    model = _model_name(llm)

    split_ids = json.loads((out_dir / SPLITS).read_text())
    rows = {r["call_id"]: r for r in read_jsonl(out_dir / GOLD)}
    wanted = [cid for name in splits for cid in split_ids[name]]
    done = load_teacher_labels(out_dir, {cid: r["text"] for cid, r in rows.items()}, compact=True)
    pending = [cid for cid in wanted if cid not in done]
    skipped = len(wanted) - len(pending)
    if limit is not None:
        pending = pending[:limit]
    log(f"teacher={model}: {len(wanted)} wanted, {skipped} already labeled, {len(pending)} to label")

    stats = {"model": model, "wanted": len(wanted), "skipped": skipped, "labeled": 0, "failed": 0, "aborted": False}
    failures: list[dict] = []
    lock = threading.Lock()
    stop = threading.Event()
    streak = 0  # consecutive failures, shared by the workers
    split_of = {cid: name for name, ids in split_ids.items() for cid in ids}

    def work(cid: str) -> None:
        nonlocal streak
        if stop.is_set():
            return
        text = rows[cid]["text"]
        try:
            lab, attempts = label_one(llm, text, max_attempts=max_attempts, sleep=sleep)
        except LabelError as exc:
            with lock:
                streak += 1
                stats["failed"] += 1
                failures.append({"call_id": cid, "error": str(exc)})
                if streak >= max_consecutive_failures and not stop.is_set():
                    stop.set()
                    stats["aborted"] = True
                    log(f"aborting: {streak} failures in a row; last: {exc}")
            return
        record = {
            "call_id": cid,
            "split": split_of[cid],
            "label": lab,
            "model": model,
            "attempts": attempts,
            "text_sha256": sha256_text(text),
        }
        with lock:
            streak = 0
            stats["labeled"] += 1
            with open(out_dir / TEACHER, "a") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            n = stats["labeled"] + stats["failed"]
            if n % 25 == 0:
                log(f"  {n}/{len(pending)} (labeled {stats['labeled']}, failed {stats['failed']})")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, pending))

    if failures:
        write_jsonl(out_dir / FAILURES, failures)
    else:
        (out_dir / FAILURES).unlink(missing_ok=True)
    finalize(out_dir)
    log(f"done: labeled {stats['labeled']}, failed {stats['failed']}, skipped {skipped}")
    return stats


# ── finalize: train/dev files, review sample, manifest ────────────────────────


def _pairs(label: dict) -> set[tuple[str, str]]:
    return {(m["name"], m["value"]) for m in label["metric_mentioned"]}


def _f1(pred: set, gold: set) -> float:
    if not pred and not gold:
        return 1.0
    hit = len(pred & gold)
    if hit == 0:
        return 0.0
    p, r = hit / len(pred), hit / len(gold)
    return 2 * p * r / (p + r)


def field_agreement(teacher: dict, gold: dict) -> dict[str, float]:
    """Per-field agreement of one teacher label with its gold label (1.0 = agree)."""
    return {
        "reason_for_call": float(teacher["reason_for_call"] == gold["reason_for_call"]),
        "product_line": float(teacher["product_line"] == gold["product_line"]),
        "resolution": float(teacher["resolution"] == gold["resolution"]),
        "follow_up_needed": float(teacher["follow_up"]["needed"] == gold["follow_up"]["needed"]),
        "metric_pair_f1": _f1(_pairs(teacher), _pairs(gold)),
        "metric_value_f1": _f1(
            {v for _, v in _pairs(teacher)}, {v for _, v in _pairs(gold)}
        ),
    }


def review_order(call_id: str) -> str:
    return hashlib.sha256(f"review_v1:{call_id}".encode()).hexdigest()


def pick_review_sample(rows: list[dict], size: int = REVIEW_SAMPLE_SIZE) -> list[dict]:
    """`size` rows spread over categories: a floor per category, the rest proportional, hash-ordered."""
    if len(rows) <= size:
        return sorted(rows, key=lambda r: review_order(r["call_id"]))
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    for rs in by_cat.values():
        rs.sort(key=lambda r: review_order(r["call_id"]))
    quota = {c: min(REVIEW_MIN_PER_CATEGORY, len(rs)) for c, rs in by_cat.items()}
    # Hand out the remaining slots one at a time to the category furthest below its proportional share.
    for _ in range(size - sum(quota.values())):
        open_cats = [c for c in by_cat if quota[c] < len(by_cat[c])]
        if not open_cats:
            break
        target = lambda c: size * len(by_cat[c]) / len(rows)  # noqa: E731
        best = max(open_cats, key=lambda c: (target(c) - quota[c], c))
        quota[best] += 1
    picked = [r for c in sorted(by_cat) for r in by_cat[c][: quota[c]]]
    return sorted(picked, key=lambda r: review_order(r["call_id"]))


def finalize(out_dir: Path = DEFAULT_OUT_DIR) -> dict:
    """Write train.jsonl / dev.jsonl (teacher-labeled calls only), the review sample and the manifest."""
    gold_rows = {r["call_id"]: r for r in read_jsonl(out_dir / GOLD)}
    split_ids = json.loads((out_dir / SPLITS).read_text())
    teacher = load_teacher_labels(out_dir, {cid: r["text"] for cid, r in gold_rows.items()})

    labeled: list[dict] = []
    for name in TEACHER_SPLITS:
        rows = []
        for cid in split_ids[name]:
            if cid in teacher:
                g = gold_rows[cid]
                rows.append(_example(g, g["gold"], teacher[cid]["label"]))
                labeled.append({**g, "split": name, "teacher": teacher[cid]["label"]})
        write_jsonl(out_dir / SPLIT_FILES[name], rows)

    sample = pick_review_sample(labeled)
    write_jsonl(
        out_dir / REVIEW,
        [
            {
                "call_id": r["call_id"],
                "category": r["category"],
                "split": r["split"],
                "text": r["text"],
                "teacher": r["teacher"],
                "gold": r["gold"],
                "agreement": field_agreement(r["teacher"], r["gold"]),
                "review": {"teacher_ok": None, "notes": ""},
            }
            for r in sample
        ],
    )
    return write_manifest(out_dir)


# ── manifest ──────────────────────────────────────────────────────────────────


def write_manifest(out_dir: Path, source_sha256: str | None = None) -> dict:
    """Describe what is on disk: counts, hashes, and teacher-vs-gold agreement.

    `dataset_sha256` hashes the schema, rules and data files only (no timestamps),
    so it changes exactly when the dataset does.
    """
    old = json.loads((out_dir / MANIFEST).read_text()) if (out_dir / MANIFEST).exists() else {}
    gold_rows = {r["call_id"]: r for r in read_jsonl(out_dir / GOLD)}
    split_ids = json.loads((out_dir / SPLITS).read_text())
    teacher = load_teacher_labels(out_dir, {cid: r["text"] for cid, r in gold_rows.items()})

    splits = {}
    for name in SPLIT_NAMES:
        ids = split_ids[name]
        splits[name] = {
            "n": len(ids),
            "by_category": dict(sorted(Counter(gold_rows[c]["category"] for c in ids).items())),
        }
    teacher_info: dict[str, Any] = {"models": sorted({t["model"] for t in teacher.values()}), "splits": {}}
    agree_sums: Counter = Counter()
    n_agree = 0
    for name in TEACHER_SPLITS:
        have = [c for c in split_ids[name] if c in teacher]
        teacher_info["splits"][name] = {"labeled": len(have), "missing": len(split_ids[name]) - len(have)}
        for c in have:
            n_agree += 1
            agree_sums.update(field_agreement(teacher[c]["label"], gold_rows[c]["gold"]))
    teacher_info["agreement_with_gold"] = {
        "n": n_agree,
        **{k: round(v / n_agree, 4) for k, v in sorted(agree_sums.items())},
    } if n_agree else {"n": 0}
    teacher_info["attempts"] = dict(sorted(Counter(t["attempts"] for t in teacher.values()).items()))

    files = {
        name: sha256_file(out_dir / name)
        for name in [GOLD, SPLITS, *SPLIT_FILES.values()]
        if (out_dir / name).exists()
    }
    schema_hash, rules_hash = schema_sha256(), sha256_file(GOLD_RULES_PATH)
    dataset_hash = sha256_text(
        json.dumps({"schema": schema_hash, "gold_rules": rules_hash, "files": files}, sort_keys=True)
    )
    manifest = {
        "schema": SCHEMA_VERSION,
        "schema_sha256": schema_hash,
        "gold_rules_sha256": rules_hash,
        "source_transcripts_sha256": source_sha256 or old.get("source_transcripts_sha256"),
        "n_calls": len(gold_rows),
        "splits": splits,
        "teacher": teacher_info,
        "files": files,
        "dataset_sha256": dataset_hash,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _write_atomic(out_dir / MANIFEST, json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m models.finetune.data_gen", description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="generator output (default data/synthetic)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="output dir (default data/finetune)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="gold labels, splits and test.jsonl (offline)")
    p_label = sub.add_parser("label", help="teacher labels for train and dev (API spend)")
    p_label.add_argument("--provider", help="LLM provider; default the LLM_PROVIDER env var")
    p_label.add_argument("--limit", type=int, help="label at most N pending calls (smoke test)")
    p_label.add_argument("--workers", type=int, default=4, help="concurrent requests (default 4)")
    p_label.add_argument("--dry-run", action="store_true", help="count pending calls and exit; no API call")
    sub.add_parser("finalize", help="rebuild train/dev, review sample and manifest from labels on disk")
    args = parser.parse_args(argv)

    if args.command == "build":
        manifest = build(args.data_dir, args.out)
        print(json.dumps({"n_calls": manifest["n_calls"], "splits": {k: v["n"] for k, v in manifest["splits"].items()},
                          "dataset_sha256": manifest["dataset_sha256"]}, indent=1))
    elif args.command == "label":
        if not (args.out / SPLITS).exists():
            build(args.data_dir, args.out)
        if args.dry_run:
            rows = {r["call_id"]: r["text"] for r in read_jsonl(args.out / GOLD)}
            done = load_teacher_labels(args.out, rows)
            ids = json.loads((args.out / SPLITS).read_text())
            pending = [c for s in TEACHER_SPLITS for c in ids[s] if c not in done]
            print(f"{len(pending)} calls pending of {sum(len(ids[s]) for s in TEACHER_SPLITS)}; no API call made")
            return 0
        stats = label(args.out, provider=args.provider, limit=args.limit, workers=args.workers)
        return 1 if stats["aborted"] else 0
    else:
        finalize(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
