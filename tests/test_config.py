"""
Env-config resolution tests for the LLM and embedding models.

These assert that `get_llm()` / `get_embeddings()` read their model, base_url,
and key from the environment — never from hardcoded values — and construct the
OpenAI-compatible / Ollama clients with those settings. No network is touched:
constructing the LangChain client objects is purely local.
"""

from types import SimpleNamespace

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

import ccai_mcp.tools as tools
import rag.pipeline as pipeline
from retrieval import Hit
from rag.embeddings import get_embeddings
from rag.pipeline import get_llm


# ── Chat LLM ──────────────────────────────────────────────────────────────────

def test_get_llm_defaults_to_low_cost_openrouter(monkeypatch):
    # No provider/model/base_url set → low-cost OpenRouter defaults.
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    llm = get_llm()

    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "z-ai/glm-5.3-flash"  # low-cost OpenRouter model
    assert llm.openai_api_base == "https://openrouter.ai/api/v1"


def test_get_llm_resolution_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_MODEL", "deepseek/deepseek-v4-flash-latest")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "x" * 32)

    llm = get_llm()

    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "deepseek/deepseek-v4-flash-latest"
    assert llm.openai_api_base == "https://openrouter.ai/api/v1"
    # The key is read from the environment only — never hardcoded.
    assert llm.openai_api_key.get_secret_value() == "sk-" + "x" * 32


def test_get_llm_ollama_provider(monkeypatch):
    monkeypatch.setattr("rag.ollama_models.ensure_ready", lambda model: None)  # no server here
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2")

    from langchain_ollama import ChatOllama

    llm = get_llm()

    assert isinstance(llm, ChatOllama)
    assert llm.model == "llama3.2"


def test_ollama_chat_output_is_capped_and_the_cap_is_configurable(monkeypatch):
    monkeypatch.setattr("rag.ollama_models.ensure_ready", lambda model: None)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_NUM_PREDICT", raising=False)

    assert get_llm().num_predict == 2048

    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "512")
    assert get_llm().num_predict == 512


# ── Embeddings ────────────────────────────────────────────────────────────────

def test_get_embeddings_defaults_openai_compatible(monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    embeddings = get_embeddings()

    assert isinstance(embeddings, OpenAIEmbeddings)
    assert embeddings.model == "text-embedding-3-small"


def test_get_embeddings_resolution_from_environment(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    embeddings = get_embeddings()

    assert isinstance(embeddings, OpenAIEmbeddings)
    assert embeddings.model == "text-embedding-3-large"
    assert embeddings.openai_api_base == "https://api.openai.com/v1"


def test_get_embeddings_ollama_provider(monkeypatch):
    monkeypatch.setattr("rag.ollama_models.ensure_ready", lambda model: None)  # no server here
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")

    from langchain_ollama import OllamaEmbeddings

    embeddings = get_embeddings()

    assert isinstance(embeddings, OllamaEmbeddings)
    assert embeddings.model == "nomic-embed-text"


# ── Known-bug regressions (see data/ccai-demo-script/report.md Findings) ──────

def test_get_call_summary_grounds_prompt_in_retrieved_doc(monkeypatch):
    # Regression: get_call_summary used to summarise without ever handing the
    # retrieved transcript to the LLM. The prompt must contain doc.page_content.
    doc = SimpleNamespace(
        page_content="MEMBER SAID: I want a lower interest rate.",
        metadata={
            "call_id": "CALL-00042",
            "date": "2026-08-01",
            "category": "loan_inquiry",
            "outcome": "resolved",
        },
    )

    captured = {}

    class _Retriever:
        def get_by_id(self, call_id):
            assert call_id == "CALL-00042"
            return Hit(call_id=call_id, text=doc.page_content, score=1.0, metadata=doc.metadata)

    class _LLM:
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return SimpleNamespace(content="summarised")

    monkeypatch.setattr(tools, "get_retriever", lambda: _Retriever())
    monkeypatch.setattr(tools, "get_llm", lambda: _LLM())

    result = tools.get_call_summary("CALL-00042")

    assert result == "summarised"
    assert doc.page_content in captured["prompt"]


def _transcript_retriever(monkeypatch, text, hit=True):
    class _Retriever:
        def get_by_id(self, call_id):
            if not hit:
                return None
            meta = {
                "call_id": call_id, "date": "2026-08-01", "duration_seconds": 425,
                "category": "loan_inquiry", "outcome": "resolved",
                "agent_id": "AGT-007", "member_id": "MBR-000584",
            }
            return Hit(call_id=call_id, text=text, score=1.0, metadata=meta)

    def _no_llm():
        raise AssertionError("get_transcript must not call an LLM")

    monkeypatch.setattr(tools, "get_retriever", lambda: _Retriever())
    monkeypatch.setattr(tools, "get_llm", _no_llm)


def test_get_transcript_returns_header_and_verbatim_dialogue(monkeypatch):
    dialogue = "AGENT: Hello.\nMEMBER: I want a lower rate."
    _transcript_retriever(monkeypatch, dialogue)

    result = tools.get_transcript("CALL-00042")

    assert "Call ID: CALL-00042" in result
    assert "Date: 2026-08-01" in result
    assert "Duration: 7:05" in result
    assert "Category: loan_inquiry" in result
    assert "Outcome: resolved" in result
    assert "Agent: AGT-007" in result
    assert "Member: MBR-000584" in result
    assert result.endswith(dialogue)


def test_get_transcript_not_found(monkeypatch):
    _transcript_retriever(monkeypatch, "", hit=False)

    assert tools.get_transcript("CALL-99999") == "No transcript found for call ID: CALL-99999"


def test_get_transcript_masks_account_numbers(monkeypatch):
    _transcript_retriever(monkeypatch, "MEMBER: My account is 123456789012.")

    result = tools.get_transcript("CALL-00042")

    assert "123456789012" not in result
    assert "****9012" in result
    assert "CALL-00042" in result and "MBR-000584" in result


def test_transcripts_to_documents_carries_olap_ids(monkeypatch):
    # Regression: RAG transcript metadata must carry interaction_id +
    # account_id so RAG answers and OLAP aggregates cite the same member.
    monkeypatch.setattr(
        pipeline,
        "_load_olap_ids",
        lambda *_: (
            {"CALL-00001": 1001},
            {1001: [42, 43]},  # subject-role account first, then referenced
        ),
    )
    monkeypatch.setattr(pipeline, "_load_home_branches", lambda *_: {"MBR-000001": "Westside"})

    docs = pipeline.transcripts_to_documents(
        [
            {
                "call_id": "CALL-00001",
                "date": "2026-08-01",
                "duration_seconds": 120,
                "category": "fraud_dispute",
                "outcome": "resolved",
                "member_id": "MBR-000001",
                "agent_id": "AGENT-0001",
                "full_text": "hello world",
            }
        ]
    )

    assert len(docs) == 1
    meta = docs[0].metadata
    assert meta["interaction_id"] == 1001
    assert meta["home_branch"] == "Westside"
    assert meta["account_id"] == 42
    assert meta["account_ids"] == [42, 43]
    assert docs[0].page_content == "hello world"

# ── P1 provider registry ──────────────────────────────────────────────────────

def test_get_llm_anthropic_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "x" * 24)
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    from langchain_anthropic import ChatAnthropic

    llm = get_llm()

    assert isinstance(llm, ChatAnthropic)
    assert llm.model == "claude-sonnet-4-5"
    assert llm.anthropic_api_key.get_secret_value() == "sk-ant-" + "x" * 24


def test_get_llm_fireworks_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fireworks")
    monkeypatch.setenv("FIREWORKS_API_KEY", "fw-" + "x" * 24)
    monkeypatch.setenv("FIREWORKS_MODEL", "accounts/fireworks/models/my-model")

    from langchain_openai import ChatOpenAI

    llm = get_llm()

    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "accounts/fireworks/models/my-model"
    assert llm.openai_api_base == "https://api.fireworks.ai/inference/v1"
    # Fireworks uses its own key, never OPENAI_API_KEY.
    assert llm.openai_api_key.get_secret_value() == "fw-" + "x" * 24


def test_get_llm_vllm_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("VLLM_MODEL", "meta-llama/Llama-3.2-3B-Instruct")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    from langchain_openai import ChatOpenAI

    llm = get_llm()

    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "meta-llama/Llama-3.2-3B-Instruct"
    assert llm.openai_api_base == "http://localhost:8000/v1"


def test_get_llm_explicit_provider_overrides_env(monkeypatch):
    # Evals loop providers in one process via the `provider` argument,
    # without mutating the environment.
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    from langchain_openai import ChatOpenAI

    llm = get_llm("openai")

    assert isinstance(llm, ChatOpenAI)
    assert llm.openai_api_base == "https://openrouter.ai/api/v1"


def test_hosted_chat_output_is_capped_and_the_cap_is_configurable(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)

    assert get_llm().max_tokens == 2048

    monkeypatch.setenv("LLM_MAX_TOKENS", "512")
    assert get_llm().max_tokens == 512


def test_hosted_chat_requests_low_reasoning_effort_unless_disabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    assert get_llm().extra_body == {"reasoning": {"effort": "low"}}

    monkeypatch.setenv("LLM_REASONING_EFFORT", "medium")
    assert get_llm().extra_body == {"reasoning": {"effort": "medium"}}

    for off in ("none", ""):
        monkeypatch.setenv("LLM_REASONING_EFFORT", off)
        assert get_llm().extra_body is None
