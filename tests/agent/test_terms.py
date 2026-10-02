"""agent/data/ambiguous_terms.yml stays consistent with the metric catalog."""

from agent.terms import load_terms, match_terms
from ccai_mcp.metrics import load_metric_catalog


def test_every_candidate_is_a_declared_metric():
    declared = set(load_metric_catalog())
    for term in load_terms().values():
        missing = set(term.candidates) - declared
        assert not missing, f"term {term.name!r} lists metrics missing from metrics.yml: {missing}"


def test_starter_terms_and_sizes():
    sizes = {name: len(t.candidates) for name, t in load_terms().items()}
    assert sizes == {"rate": 7, "balance": 10, "lcv": 2}


def test_aliases_are_whole_words():
    assert match_terms("how is the corporate ratio?") == []
    assert [m.term for m in match_terms("what is our LTV?")] == ["lcv"]
    assert [m.term for m in match_terms("show LCV and average balance")] == ["lcv", "balance"]


def test_qualifiers_narrow_candidates():
    (match,) = match_terms("average mortgage note rate")
    assert match.candidates == ["average_mortgage_note_rate"]
    (match,) = match_terms("what is the mortgage rate")
    assert match.candidates == ["average_mortgage_note_rate", "weighted_mortgage_portfolio_rate"]
    (match,) = match_terms("what is the average rate")
    assert len(match.candidates) == 7


def test_a_metric_named_by_its_label_wins_over_qualifier_ties():
    (match,) = match_terms("What is the banking ledger balance?")
    assert match.candidates == ["banking_ledger_balance"]
    (match,) = match_terms("What is the checking available balance?")
    assert match.candidates == ["checking_available_balance"]


def test_genuinely_ambiguous_questions_still_offer_every_candidate():
    (match,) = match_terms("what is the balance?")
    assert len(match.candidates) > 1
    (match,) = match_terms("what is the savings or checking balance?")
    assert len(match.candidates) > 1
