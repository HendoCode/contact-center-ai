"""Make sure an Ollama model is on the server before anything uses it.

`rag.embeddings.get_embeddings` and `rag.pipeline.get_llm` call `ensure_ready` on every
Ollama model they build, so ingest, search, the MCP tools, the agent, evals and the bench
all get the same guard. Only `make demo` pulls models (the Compose `ollama-pull` service);
after a bare `make up`, or once the Ollama volume loses its models, the first call would
otherwise fail with a 404 "model not found".

A missing model is pulled once, with one line saying so. An unreachable server raises
`OllamaUnavailable` with one actionable line. A model confirmed present is remembered for
the rest of the process, so repeated factory calls ask the server once.
"""

from __future__ import annotations

DEFAULT_BASE_URL = "http://localhost:11434"

# Rough download sizes for the models this repo defaults to, for the "pulling" line.
SIZES = {"nomic-embed-text": "~270 MB", "llama3.2": "~2 GB"}

_ready: set[tuple[str, str]] = set()


class OllamaUnavailable(RuntimeError):
    """Ollama cannot serve the model; the message says what to do."""


def _tagged(model: str) -> str:
    return model if ":" in model else f"{model}:latest"


def ensure_model(model: str, base_url: str | None = None, client=None) -> None:
    """Pull `model` when the Ollama server at `base_url` lacks it; a no-op once present."""
    base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    if (base_url, model) in _ready:
        return
    if client is None:
        import ollama

        client = ollama.Client(host=base_url)
    try:
        have = {m.model for m in client.list().models}
    except Exception as exc:  # ollama raises ConnectionError; httpx errors can leak through
        raise OllamaUnavailable(
            f"Ollama is not reachable at {base_url}: start it with 'make up' "
            "(or point OLLAMA_BASE_URL at a running server)") from exc
    if _tagged(model) not in have:
        print(f"pulling {model} ({SIZES.get(model.split(':')[0], 'one-time download')})", flush=True)
        try:
            client.pull(model)
        except Exception as exc:
            raise OllamaUnavailable(f"Ollama at {base_url} could not pull {model}: {exc}") from exc
    _ready.add((base_url, model))


def ensure_ready(model_obj, client=None) -> None:
    """`ensure_model` for a LangChain OllamaEmbeddings or ChatOllama; other providers pass."""
    if type(model_obj).__name__ not in ("OllamaEmbeddings", "ChatOllama"):
        return
    ensure_model(model_obj.model, getattr(model_obj, "base_url", None), client=client)
