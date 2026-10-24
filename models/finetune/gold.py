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
template (`CATEGORY_DIALOGUES` in `dialogue_templates.py`): a category has several variants
that word the same facts differently, and a call must match one of them. A call that
matches none means the generator and these rules have drifted apart, and the build
fails loudly instead of emitting a wrong label.

Only baseline calls are labeled. The generator also plants dated events, whose calls
use other dialogue; those calls are dropped using the ground-truth file the generator
writes (`ground_truth/events.json`), so a model fine-tuned on this corpus never sees
the planted drivers. The corpus stays the 1,250 baseline calls.
"""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from models.finetune.schema import CATEGORIES, METRIC_NAMES, PRODUCT_LINES, RESOLUTIONS, validate_summary

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "synthetic"
GOLD_RULES_PATH = Path(__file__).with_name("gold_rules.yml")
TEMPLATES_PATH = DEFAULT_DATA_DIR / "dialogue_templates.py"

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


Template = list[tuple[str, str]]


def load_dialogue_templates(templates_path: Path = TEMPLATES_PATH) -> dict[str, list[Template]]:
    """The generator's `CATEGORY_DIALOGUES` (category -> variants), imported from its
    template module, which runs no generation."""
    spec = importlib.util.spec_from_file_location("_ccai_dialogue_templates", templates_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CATEGORY_DIALOGUES


def event_call_ids(data_dir: Path = DEFAULT_DATA_DIR) -> set[str]:
    """Call ids the generator planted for its dated events (everything else is baseline)."""
    path = data_dir / "ground_truth" / "events.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run: python data/synthetic/generate_data.py "
            "(it writes the file that tells baseline calls from event calls)"
        )
    return set(json.loads(path.read_text())["calls"])


def load_calls(data_dir: Path = DEFAULT_DATA_DIR) -> list[CallRecord]:
    """Read the generator output (baseline calls only) and join each call to its subject
    account's line of business."""
    read = lambda name: json.loads((data_dir / name).read_text())  # noqa: E731
    events = event_call_ids(data_dir)
    transcripts = [t for t in read("transcripts.json") if t["call_id"] not in events]
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


def _facts_from_variant(call: CallRecord, template: Template) -> dict[str, str]:
    """The facts spoken in `call` under one variant, or ValueError if the call does not fit it."""
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


def spoken_facts(call: CallRecord, templates: dict[str, list[Template]]) -> dict[str, str]:
    """Recover the template facts spoken in the call, matched turn by turn against any variant
    of its category's dialogue. Raises ValueError when no variant fits."""
    errors = []
    for template in templates[call.category]:
        try:
            return _facts_from_variant(call, template)
        except ValueError as exc:
            errors.append(str(exc))
    raise ValueError(errors[0] if len(errors) == 1 else f"{errors[0]} (no variant of {call.category} fits)")


def build_gold(call: CallRecord, rules: dict[str, Any], templates: dict[str, list[Template]]) -> dict:
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
