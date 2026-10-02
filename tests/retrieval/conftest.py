"""Offline fixtures for the LanceDB backend: a deterministic fake embedding and 50 documents."""

import hashlib
import math
import re

import pytest

pytest.importorskip("lancedb", reason="install the lance group: uv sync --extra dev --group lance")

from retrieval.lancedb_backend import LanceDBRetriever  # noqa: E402

DIM = 64


class FakeEmbeddings:
    """Hashed bag-of-words, L2-normalised: texts sharing words are close, no network or key."""

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


CATEGORIES = {
    "fraud_dispute": "member reports an unauthorized card charge and disputes the transaction",
    "loan_inquiry": "member asks about auto loan rates and the application process",
    "balance_inquiry": "member wants the current checking account balance and recent deposits",
    "card_replacement": "member lost a debit card and requests a replacement card",
    "online_banking": "member cannot log in to online banking and needs a password reset",
}
OUTCOMES = ("resolved", "escalated", "follow_up")


def make_docs(n: int = 50) -> list[dict]:
    names = list(CATEGORIES)
    docs = []
    for i in range(n):
        category = names[i % len(names)]
        docs.append(
            {
                "call_id": f"CALL-{i:05d}",
                "text": f"Call {i}. {CATEGORIES[category]}. Reference token zq{i:03d}.",
                "metadata": {
                    "category": category,
                    "outcome": OUTCOMES[i % len(OUTCOMES)],
                    "date": f"2026-0{1 + i % 9}-{1 + i % 28:02d}",
                    "agent_id": f"AGT-{i % 7}",
                },
            }
        )
    return docs


@pytest.fixture
def docs():
    return make_docs()


@pytest.fixture
def docs_factory():
    return make_docs


@pytest.fixture
def fake_embeddings():
    return FakeEmbeddings()


@pytest.fixture
def make_retriever(tmp_path):
    def _make(**kwargs):
        return LanceDBRetriever(uri=str(tmp_path / "lance"), embeddings=FakeEmbeddings(), **kwargs)

    return _make


@pytest.fixture
def retriever(make_retriever, docs):
    r = make_retriever()
    r.ingest(docs)
    return r
