"""Dialogue variants: wording changes, facts do not; keywords mark drivers; account numbers are masked."""

import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = re.compile(r"\{(\w+)\}")


@pytest.fixture(scope="module")
def templates():
    spec = importlib.util.spec_from_file_location(
        "_dialogue_templates", REPO_ROOT / "data" / "synthetic" / "dialogue_templates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IDENTIFIERS = {"member_id", "member_name", "account_last4"}   # who is calling, not what is said


def facts_of(template) -> list[str]:
    return [k for _, text in template for k in PLACEHOLDER.findall(text)]


def figures_of(template) -> tuple[str, ...]:
    return tuple(sorted(set(facts_of(template)) - IDENTIFIERS))


def test_every_category_has_at_least_three_variants_that_speak_the_same_facts(templates):
    assert len(templates.CATEGORY_DIALOGUES) == 14
    for category, variants in templates.CATEGORY_DIALOGUES.items():
        assert len(variants) >= 3, category
        assert len({tuple(sorted(set(facts_of(v)))) for v in variants}) == 1, category
        assert len({" ".join(text for _, text in v) for v in variants}) == len(variants)


def test_event_intros_and_bodies_have_three_variants_with_the_same_facts(templates):
    groups = [*templates.INTROS.values(), *templates.BODIES.values(),
              *templates.MISSTATED_REFUSALS.values()]
    assert groups
    for variants in groups:
        assert len(variants) == 3
        assert len({figures_of(v) for v in variants}) == 1, variants[0]


def test_every_driver_in_events_yml_has_dialogue_for_each_of_its_categories(templates, events_config):
    for ev in events_config["events"]:
        for driver in ev["drivers"]:
            assert driver["id"] in templates.BODIES, driver["id"]
            for category in driver["share"]:
                assert (ev["id"], category) in templates.INTROS, (ev["id"], category)
            for pattern in driver.get("patterns", []):
                assert set(pattern["variants"]) == set(templates.MISSTATED_REFUSALS)


def test_no_variant_introduces_the_agent_by_name(templates):
    for variants in templates.CATEGORY_DIALOGUES.values():
        for variant in variants:
            for speaker, text in variant:
                assert speaker in ("member", "agent")
                assert "my name is" not in text.lower() and "this is " not in text.lower()


def test_each_driver_has_keywords_in_its_calls_and_baseline_calls_have_none(corpus, events_config):
    keywords = {d["id"]: [k.lower() for k in d["keywords"]]
                for ev in events_config["events"] for d in ev["drivers"]}
    planted = corpus.truth["calls"]
    for cid, label in planted.items():
        text = corpus.transcripts[cid]["full_text"].lower()
        assert any(k in text for k in keywords[label["driver"]]), (cid, label["driver"])
    all_keywords = {k for words in keywords.values() for k in words}
    for cid, t in corpus.transcripts.items():
        if cid not in planted:
            text = t["full_text"].lower()
            assert not [k for k in all_keywords if k in text], cid


def test_pattern_calls_say_their_misstatement(corpus, events_config):
    pattern = events_config["events"][0]["drivers"][1]["patterns"][0]
    for cid, label in corpus.truth["calls"].items():
        if label["pattern"]:
            text = corpus.transcripts[cid]["full_text"].lower()
            assert any(k in text for k in pattern["keywords"][label["variant"]]), (cid, label)


def test_account_numbers_are_masked_to_the_last_four_digits(corpus):
    for cid, t in corpus.transcripts.items():
        text = t["full_text"]
        assert re.findall(r"(?:ending|ends) in (\d+)", text) == [
            d for d in re.findall(r"(?:ending|ends) in (\d{4})\b", text)], cid
        stripped = re.sub(r"MBR-\d+|\$[\d,]+(?:\.\d+)?|\d+(?:\.\d+)?%|\d{4}-\d{2}-\d{2}\S*", " ", text)
        assert not re.search(r"\b\d{5,}\b", stripped), cid


def test_dialogue_uses_every_variant_of_every_category(corpus, templates):
    seen = {}
    for cid, t in corpus.transcripts.items():
        if cid in corpus.truth["calls"]:
            continue
        for i, variant in enumerate(templates.CATEGORY_DIALOGUES[t["category"]]):
            if t["transcript"][0]["text"] == variant[0][1]:
                seen.setdefault(t["category"], set()).add(i)
    assert set(seen) == set(templates.CATEGORY_DIALOGUES)
    assert all(v == {0, 1, 2} for v in seen.values())


def test_each_event_category_uses_all_three_wordings_across_the_corpus(corpus):
    openers, calls = {}, {}
    for cid in corpus.truth["calls"]:
        t = corpus.transcripts[cid]
        key = (corpus.truth["calls"][cid]["event"], t["category"])
        openers.setdefault(key, set()).add(t["transcript"][0]["text"])
        calls[key] = calls.get(key, 0) + 1
    assert len(openers) == 8
    for key, words in openers.items():
        # a handful of calls may not draw every wording; any real volume does
        assert len(words) == 3 or calls[key] < 12, (key, calls[key], len(words))
