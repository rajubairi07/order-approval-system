"""LLM provider abstraction (spec decision D9).

One env-selected chat model powers all agents. "mock" returns a deterministic
scripted model so every workflow and test runs with zero API keys; real
providers go through LangChain's init_chat_model, which resolves credentials
from standard environment variables.
"""

from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import get_settings

_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-5",
    "google_genai": "gemini-2.5-flash",
    "ollama": "llama3.1",
}


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    """Build (once) and reuse the configured chat model: provider clients keep
    HTTP connection pools that should outlive a single request."""
    settings = get_settings()
    provider = settings.llm_provider.lower()

    if provider == "mock":
        from app.llm.mock import MockChatModel

        return MockChatModel()

    if provider not in _DEFAULT_MODELS:
        raise ValueError(
            f"Unknown LLM_PROVIDER '{provider}'. "
            f"Expected one of: mock, {', '.join(_DEFAULT_MODELS)}"
        )

    from langchain.chat_models import init_chat_model

    model = settings.llm_model or _DEFAULT_MODELS[provider]
    return init_chat_model(f"{provider}:{model}", temperature=0)
