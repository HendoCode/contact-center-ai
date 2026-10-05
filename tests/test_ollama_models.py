"""The shared Ollama model guard (rag/ollama_models.py) and the factories that call it.

A fake Ollama client stands in for the server: no network, no model download.
"""

import pytest

from rag import ollama_models
from rag.ollama_models import OllamaUnavailable, ensure_model, ensure_ready


class _Model:
    def __init__(self, model):
        self.model = model


class FakeOllama:
    def __init__(self, have=(), reachable=True):
        self.have, self.reachable = list(have), reachable
        self.pulled, self.listed = [], 0

    def list(self):
        self.listed += 1
        if not self.reachable:
            raise ConnectionError("Failed to connect to Ollama.")
        return type("ListResponse", (), {"models": [_Model(m) for m in self.have]})()

    def pull(self, model):
        self.pulled.append(model)
        self.have.append(f"{model}:latest")


class OllamaEmbeddings:  # matched by class name, like langchain_ollama's
    model, base_url = "nomic-embed-text", "http://localhost:11434"


@pytest.fixture(autouse=True)
def fresh_guard(monkeypatch):
    monkeypatch.setattr(ollama_models, "_ready", set())


def test_missing_model_is_pulled_once(capsys):
    client = FakeOllama(have=["llama3.2:latest"])
    ensure_model("nomic-embed-text", "http://localhost:11434", client=client)
    ensure_model("nomic-embed-text", "http://localhost:11434", client=client)
    assert client.pulled == ["nomic-embed-text"]
    assert client.listed == 1  # confirmed once per process, then remembered
    assert capsys.readouterr().out == "pulling nomic-embed-text (~270 MB)\n"


def test_present_model_is_not_pulled(capsys):
    client = FakeOllama(have=["nomic-embed-text:latest"])
    ensure_model("nomic-embed-text", client=client)
    assert client.pulled == []
    assert capsys.readouterr().out == ""


def test_tagged_model_names_match_exactly():
    client = FakeOllama(have=["llama3.2:latest"])
    ensure_model("llama3.2:1b", client=client)
    assert client.pulled == ["llama3.2:1b"]


def test_unreachable_server_is_one_actionable_line():
    with pytest.raises(OllamaUnavailable) as err:
        ensure_model("nomic-embed-text", "http://ollama:11434/", client=FakeOllama(reachable=False))
    msg = str(err.value)
    assert msg == ("Ollama is not reachable at http://ollama:11434: start it with 'make up' "
                   "(or point OLLAMA_BASE_URL at a running server)")
    assert "\n" not in msg


def test_ensure_ready_skips_other_providers():
    client = FakeOllama(have=[])
    ensure_ready(object(), client=client)
    assert client.listed == 0 and client.pulled == []


def test_ensure_ready_uses_the_model_and_server_of_the_object():
    client = FakeOllama(have=[])
    ensure_ready(OllamaEmbeddings(), client=client)
    assert client.pulled == ["nomic-embed-text"]


def test_get_embeddings_and_get_llm_both_guard_ollama(monkeypatch):
    seen = []
    monkeypatch.setattr(ollama_models, "ensure_ready", lambda obj: seen.append((type(obj).__name__, obj.model)))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2")
    from rag.embeddings import get_embeddings
    from rag.pipeline import get_llm

    get_embeddings()
    get_llm()
    assert seen == [("OllamaEmbeddings", "nomic-embed-text"), ("ChatOllama", "llama3.2")]


def test_other_providers_never_reach_the_guard(monkeypatch):
    monkeypatch.setattr(ollama_models, "ensure_ready", lambda obj: pytest.fail("guard called"))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from rag.embeddings import get_embeddings

    get_embeddings()


def test_preflight_reports_an_unreachable_ollama():
    from evals.preflight import PreflightError, check_ollama

    with pytest.raises(PreflightError, match="Ollama is not reachable at http://x:1.*EMBEDDING_PROVIDER"):
        check_ollama("http://x:1", "nomic-embed-text", client=FakeOllama(reachable=False))


def test_preflight_pulls_a_missing_embedding_model(capsys):
    from evals.preflight import check_ollama

    client = FakeOllama(have=[])
    assert check_ollama("http://x:1", "nomic-embed-text", client=client) == \
        "Ollama reachable at http://x:1, nomic-embed-text present"
    assert client.pulled == ["nomic-embed-text"]
