"""Phase 5: LLM provider abstraction selection logic."""

import pytest

from app.config import get_settings
from app.llm.mock import MockChatModel
from app.llm.provider import get_chat_model


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_default_provider_is_mock_no_credentials_needed(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert isinstance(get_chat_model(), MockChatModel)


def test_explicit_mock_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert isinstance(get_chat_model(), MockChatModel)


def test_unknown_provider_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "notarealllm")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_chat_model()


def test_real_providers_route_through_init_chat_model(monkeypatch):
    """Provider string reaches LangChain's init_chat_model unchanged."""
    captured = {}

    def fake_init(spec, **kwargs):
        captured["spec"] = spec
        return MockChatModel()

    import langchain.chat_models as lc

    monkeypatch.setattr(lc, "init_chat_model", fake_init)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")
    get_chat_model()
    assert captured["spec"] == "anthropic:claude-sonnet-5"
