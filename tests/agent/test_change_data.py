"""agent/data/change_*.yml stay consistent with the tool and the metric catalog."""

from agent.subgraphs.change import load_measures, load_subjects
from ccai_mcp.change import SUPPORTED_METRICS
from ccai_mcp.metrics import load_metric_catalog


def test_the_measures_are_the_metrics_the_tool_supports():
    assert tuple(load_measures()) == SUPPORTED_METRICS


def test_every_measure_is_a_declared_metric_with_phrases():
    declared = set(load_metric_catalog())
    for metric, phrases in load_measures().items():
        assert metric in declared and phrases, metric


def test_no_phrase_means_two_measures():
    seen: dict[str, str] = {}
    for metric, phrases in load_measures().items():
        for phrase in phrases:
            assert seen.setdefault(phrase.lower(), metric) == metric, phrase


def test_subject_phrases_are_unique_and_every_entry_has_categories():
    seen = set()
    for phrases, categories in load_subjects():
        assert categories and phrases
        for phrase in phrases:
            assert phrase.lower() not in seen, phrase
            seen.add(phrase.lower())
