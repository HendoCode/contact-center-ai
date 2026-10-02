"""Offline checks on the R3 labels: schema, mix, and reproducibility from generator metadata."""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from retrieval.bench import LABELS_PATH, QUERY_TYPES, load_queries, validate_query
from retrieval.labels import build_labels

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "synthetic"


@pytest.fixture(scope="module")
def queries():
    return load_queries(LABELS_PATH)


@pytest.fixture(scope="module")
def transcripts():
    """The seed-42 transcripts; generated (deterministically, offline) if absent."""
    path = DATA_DIR / "transcripts.json"
    if not path.exists():
        subprocess.run([sys.executable, "data/synthetic/generate_data.py"], cwd=REPO_ROOT,
                       check=True, capture_output=True)
    return {t["call_id"]: t for t in json.loads(path.read_text())}


# ── schema ────────────────────────────────────────────────────────────────────

def test_every_record_matches_the_label_schema(queries):
    for q in queries:
        assert validate_query(q) == [], q["id"]


def test_validate_query_rejects_bad_records():
    good = {"id": "q1", "type": "exact", "query": "x", "where": None,
            "relevant": ["CALL-00001"], "derivation": "category = x"}
    assert validate_query(good) == []
    assert validate_query({**good, "type": "vibes"})
    assert validate_query({**good, "relevant": []})
    assert validate_query({**good, "relevant": ["a", "a"]})
    assert validate_query({**good, "where": {"member_id": "x"}})  # outside portable subset
    assert validate_query({**good, "where": "category = x"})
    assert validate_query([good])


def test_query_mix_covers_every_type(queries):
    assert 35 <= len(queries) <= 45
    counts = Counter(q["type"] for q in queries)
    assert set(counts) == set(QUERY_TYPES)
    assert min(counts.values()) >= 5
    assert len({q["query"] for q in queries}) == len(queries)


# ── derivation ────────────────────────────────────────────────────────────────

def test_labels_reproduce_from_generator_metadata(transcripts):
    """queries.jsonl is exactly what build_labels derives from the seed-42 data."""
    assert build_labels.dumps(build_labels.build()) == LABELS_PATH.read_text()


def test_relevant_ids_exist_in_corpus(queries, transcripts):
    for q in queries:
        assert set(q["relevant"]) <= set(transcripts), q["id"]


def test_filter_relevant_calls_satisfy_their_where(queries, transcripts):
    """Every relevant call passes the query's own `where`, read straight off the transcript."""
    def passes(t, where):
        for fld, cond in where.items():
            v = t[fld]
            if isinstance(cond, dict):
                if "in" in cond and v not in cond["in"]:
                    return False
                if "gte" in cond and v < cond["gte"]:
                    return False
                if "lte" in cond and v > cond["lte"]:
                    return False
            elif v != cond:
                return False
        return True

    filtered = [q for q in queries if q["where"]]
    assert {q["type"] for q in filtered} == {"filter"}
    for q in filtered:
        assert all(passes(transcripts[c], q["where"]) for c in q["relevant"]), q["id"]


def test_id_queries_are_spoken_in_every_relevant_transcript(queries, transcripts):
    """Exact-phrase id queries: the id (or account last-4) appears verbatim in each relevant call."""
    for q in queries:
        if q["type"] == "exact" and (q["query"].startswith("MBR-") or "ending in" in q["query"]):
            needle = q["query"].removeprefix("account ending in ")
            for c in q["relevant"]:
                assert needle in transcripts[c]["full_text"], (q["id"], c)


def test_label_builder_never_consults_a_retriever():
    source = Path(build_labels.__file__).read_text()
    for banned in ("get_retriever", "lancedb_backend", "pgvector_backend", ".search("):
        assert banned not in source


def test_paraphrases_share_no_content_word_with_their_template():
    """Pins the labels README claim; the builder itself allows at most one shared word."""
    overlap = build_labels.paraphrase_overlap(build_labels.build())
    assert len(overlap) >= 5 and all(not words for words in overlap.values()), overlap
