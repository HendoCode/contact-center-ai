"""Ambiguous-term matching, driven by agent/data/ambiguous_terms.yml.

Pure functions, no LLM and no I/O beyond reading the YAML once.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

TERMS_PATH = Path(__file__).parent / "data" / "ambiguous_terms.yml"


@dataclass(frozen=True)
class Term:
    name: str
    aliases: tuple[str, ...]
    unless: tuple[str, ...]
    candidates: dict[str, tuple[str, ...]]  # metric name -> qualifier phrases


@dataclass(frozen=True)
class TermMatch:
    term: str
    candidates: list[str]  # after narrowing by qualifiers; one entry means unambiguous


@cache
def load_terms(path: Path = TERMS_PATH) -> dict[str, Term]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)["terms"]
    return {
        name: Term(
            name=name,
            aliases=tuple(spec["aliases"]),
            unless=tuple(spec.get("unless") or ()),
            candidates={m: tuple(q) for m, q in spec["candidates"].items()},
        )
        for name, spec in raw.items()
    }


def _phrase(text: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(text.lower())}\b")


def _narrow(term: Term, question: str) -> list[str]:
    """Keep the candidates whose qualifiers match most; all of them if none match.

    A candidate named in full (its label, e.g. "banking ledger balance") wins outright:
    the question already says which metric it means.
    """
    named = [m for m in term.candidates if _phrase(option_label(m)).search(question)]
    if named:
        longest = max(len(m) for m in named)
        return [m for m in named if len(m) == longest]
    scores = {
        metric: sum(1 for q in qualifiers if _phrase(q).search(question))
        for metric, qualifiers in term.candidates.items()
    }
    best = max(scores.values())
    if best == 0:
        return list(term.candidates)
    return [m for m, s in scores.items() if s == best]


def match_terms(question: str, skip: Iterable[str] = ()) -> list[TermMatch]:
    """Terms found in the question, in order of appearance, minus those in `skip`."""
    skipped = set(skip)
    lowered = question.lower()
    found: list[tuple[int, TermMatch]] = []
    for term in load_terms().values():
        if term.name in skipped:
            continue
        stripped = lowered
        for phrase in term.unless:
            stripped = _phrase(phrase).sub(" ", stripped)
        hits = [m for alias in term.aliases if (m := _phrase(alias).search(stripped))]
        if hits:
            found.append((min(m.start() for m in hits), TermMatch(term.name, _narrow(term, lowered))))
    return [match for _, match in sorted(found, key=lambda pair: pair[0])]


def option_label(metric: str) -> str:
    return metric.replace("_", " ").title()
