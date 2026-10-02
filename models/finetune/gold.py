"""
Gold labels for summary_v1, built by code from the generator's output plus
`gold_rules.yml`. No model is involved, so the frontier arm can be graded
against labels it had no hand in.

Where each field comes from:
  reason_for_call  the transcript's `category`
  product_line     the subject account's line of business (rules decide the exceptions)
  metric_mentioned the figures the dialogue template speaks, read back out of the
                   transcript turns so every value is verbatim in `full_text`
  resolution       per-category rule
  follow_up        per-category rule

Figures are recovered by matching each transcript turn against its dialogue
template (`CATEGORY_DIALOGUES` in the generator). A turn that does not match its
template means the generator and these rules have drifted apart, and the build
fails loudly instead of emitting a wrong label.
"""

from __future__ import annotations

import importlib.util
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from models.finetune.schema import CATEGORIES, METRIC_NAMES, PRODUCT_LINES, RESOLUTIONS, validate_summary

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "synthetic"
GOLD_RULES_PATH = Path(__file__).with_name("gold_rules.yml")
GENERATOR_PATH = DEFAULT_DATA_DIR / "generate_data.py"

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class CallRecord:
    """One transcript plus the OLTP fact the rules need (the subject account's line of business)."""

    call_id: str
    category: str
    text: str
    turns: list[dict]
    subject_lob: str | None


def load_rules(path: Path = GOLD_RULES_PATH) -> dict[str, Any]:
    """Load `gold_rules.yml` and check it covers every category with known enum values."""
    rules = yaml.safe_load(path.read_text())
    cats = rules["categories"]
    if sorted(cats) != sorted(CATEGORIES):
        raise ValueError(f"gold_rules.yml categories differ from schema CATEGORIES: {sorted(set(cats) ^ set(CATEGORIES))}")
    for cat, rule in cats.items():
        if rule["resolution"] not in RESOLUTIONS:
            raise ValueError(f"{cat}: unknown resolution {rule['resolution']!r}")
        if rule["product_line"] != "subject_lob" and rule["product_line"] not in PRODUCT_LINES:
            raise ValueError(f"{cat}: unknown product_line {rule['product_line']!r}")
        unknown = set(rule["metrics"]) - set(METRIC_NAMES)
        if unknown:
            raise ValueError(f"{cat}: unknown metric names {sorted(unknown)}")
    return rules


def load_dialogue_templates(generator_path: Path = GENERATOR_PATH) -> dict[str, list[tuple[str, str]]]:
    """Import the generator's `CATEGORY_DIALOGUES` without disturbing the caller's RNG state."""
    state = random.getstate()
    try:
        spec = importlib.util.spec_from_file_location("_ccai_generate_data", generator_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        random.setstate(state)
    return module.CATEGORY_DIALOGUES


def load_calls(data_dir: Path = DEFAULT_DATA_DIR) -> list[CallRecord]:
    """Read the generator output and join each call to its subject account's line of business."""
    read = lambda name: json.loads((data_dir / name).read_text())  # noqa: E731
    transcripts = read("transcripts.json")
    interaction_id = {i["call_id"]: i["interaction_id"] for i in read("interactions.json")}
    product_id = {a["account_id"]: a["product_id"] for a in read("accounts.json")}
    lob_of_product = {p["product_id"]: p["lob"] for p in read("products.json")}
    subject_account = {
        ia["interaction_id"]: ia["account_id"]
        for ia in read("interaction_accounts.json")
        if ia["account_role"] == "subject"
    }
    calls = []
    for t in transcripts:
        account = subject_account.get(interaction_id[t["call_id"]])
        lob = lob_of_product[product_id[account]] if account is not None else None
        calls.append(CallRecord(t["call_id"], t["category"], t["full_text"], t["transcript"], lob))
    return calls


def _template_regex(template: str) -> re.Pattern[str]:
    """Compile a dialogue template into a regex that captures each placeholder."""
    parts, seen, pos = [], set(), 0
    for m in _PLACEHOLDER.finditer(template):
        parts.append(re.escape(template[pos : m.start()]))
        key = m.group(1)
        parts.append(f"(?P={key})" if key in seen else f"(?P<{key}>.+?)")
        seen.add(key)
        pos = m.end()
    parts.append(re.escape(template[pos:]))
    return re.compile("".join(parts), re.DOTALL)


def spoken_facts(call: CallRecord, templates: dict[str, list[tuple[str, str]]]) -> dict[str, str]:
    """Recover the template facts spoken in the call, matched turn by turn against its template."""
    template = templates[call.category]
    if len(template) != len(call.turns):
        raise ValueError(f"{call.call_id}: {len(call.turns)} turns, template has {len(template)}")
    facts: dict[str, str] = {}
    for (speaker, text), turn in zip(template, call.turns):
        if speaker != turn["speaker"]:
            raise ValueError(f"{call.call_id}: speaker {turn['speaker']!r} where the template has {speaker!r}")
        match = _template_regex(text).fullmatch(turn["text"])
        if match is None:
            raise ValueError(f"{call.call_id}: turn does not match its template: {turn['text']!r}")
        for key, value in match.groupdict().items():
            if facts.setdefault(key, value) != value:
                raise ValueError(f"{call.call_id}: fact {key!r} is spoken with two values")
    return facts


def build_gold(call: CallRecord, rules: dict[str, Any], templates: dict[str, list[tuple[str, str]]]) -> dict:
    """Build the summary_v1 gold label for one call."""
    rule = rules["categories"][call.category]
    facts = spoken_facts(call, templates)
    product_line = (call.subject_lob or "unknown") if rule["product_line"] == "subject_lob" else rule["product_line"]
    action = rule["follow_up"]
    label = {
        "reason_for_call": call.category,
        "product_line": product_line,
        "metric_mentioned": [{"name": name, "value": facts[name]} for name in rule["metrics"]],
        "resolution": rule["resolution"],
        "follow_up": {"needed": action is not None, "action": action},
    }
    errors = validate_summary(label, call.text)
    if errors:
        raise ValueError(f"{call.call_id}: gold label is invalid: {errors}")
    return label


def build_all_gold(calls: list[CallRecord], rules: dict[str, Any], templates: dict) -> dict[str, dict]:
    """Gold label for every call, keyed by call_id."""
    return {call.call_id: build_gold(call, rules, templates) for call in calls}
