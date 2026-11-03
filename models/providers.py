"""Chat-model registry: `get_llm()` picks the provider, `with_output_cap()` adjusts its output cap.

`rag.pipeline` re-exports these names, so `from rag.pipeline import get_llm` keeps working.
"""

import os

import rag  # noqa: F401  (loads .env before any variable below is read)


def get_llm(provider: str | None = None):
    """
    Return the chat LLM for a provider, taking `provider` explicitly or
    falling back to the LLM_PROVIDER env var (default "openai").

    Passing `provider` explicitly lets evals loop over providers in a single
    process without mutating the environment.

    Providers:
      - "openai":    any OpenAI-compatible chat endpoint — OpenAI, OpenRouter,
                     or a self-hosted gateway — via LLM_BASE_URL + LLM_MODEL.
                     The defaults route to a low-cost model on OpenRouter, so
                     no code change is needed to get a cheap model: only
                     OPENAI_API_KEY (already read from the environment).
      - "ollama":    local Ollama via OLLAMA_MODEL + OLLAMA_BASE_URL.
      - "anthropic": Claude via ANTHROPIC_API_KEY + ANTHROPIC_MODEL
                     (default a low-cost Claude Sonnet).
      - "fireworks": Fireworks AI's OpenAI-compatible endpoint via its own
                     FIREWORKS_API_KEY + FIREWORKS_MODEL.
      - "vllm":      a self-hosted vLLM OpenAI-compatible server via
                     VLLM_BASE_URL + VLLM_MODEL (reuse OPENAI_API_KEY).
    """
    provider = (provider or os.getenv("LLM_PROVIDER", "openai")).lower()

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        from rag.ollama_models import ensure_ready
        llm = ChatOllama(
            model=os.getenv("OLLAMA_MODEL", "llama3.2"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            # A small local model can loop without ever emitting a stop token; cap the output.
            num_predict=int(os.getenv("OLLAMA_NUM_PREDICT", "2048")),
        )
        ensure_ready(llm)  # pulls the model if the server lacks it
        return llm

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )

    # Fireworks and vLLM both speak the OpenAI protocol but have their own
    # endpoint/key settings; neither may silently fall back to the "openai"
    # provider's config.
    if provider == "fireworks":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=os.getenv("FIREWORKS_MODEL", "accounts/fireworks/models/llama4-scout-instruct-basic"),
            api_key=os.getenv("FIREWORKS_API_KEY"),
            base_url="https://api.fireworks.ai/inference/v1",
        )

    if provider == "vllm":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=os.getenv("VLLM_MODEL", "meta-llama/Llama-3.2-3B-Instruct"),
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"),
        )

    from langchain_openai import ChatOpenAI
    kwargs = {}
    effort = reasoning_effort()
    if effort:
        # OpenRouter's request field; reasoning tokens count against max_tokens, so a
        # reasoning model left at its default can spend the whole cap before it answers.
        kwargs["extra_body"] = {"reasoning": {"effort": effort}}
    return ChatOpenAI(
        model=os.getenv("LLM_MODEL", "z-ai/glm-5.3-flash"),
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
        # A hosted model can write far past what a supervisor reads; cap the output.
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "2048")),
        **kwargs,
    )


def reasoning_effort() -> str | None:
    """LLM_REASONING_EFFORT for the OpenAI-compatible branch: default "low"; "none" or empty
    leaves the request unchanged."""
    value = os.getenv("LLM_REASONING_EFFORT", "low").strip().lower()
    return None if value in ("", "none") else value


def with_output_cap(llm, max_tokens: int):
    """Copy of a hosted chat model with a different output cap; any other model is returned as is."""
    if getattr(llm, "max_tokens", None) is None or not hasattr(llm, "model_copy"):
        return llm
    return llm.model_copy(update={"max_tokens": max_tokens})
