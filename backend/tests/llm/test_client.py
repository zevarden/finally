"""LiteLLM wrapper: kwargs, retry-once on bad output, and outright failure."""

import pytest

from app.llm import client
from app.llm.models import RESPONSE_FORMAT

MSGS = [{"role": "user", "content": "hi"}]


async def test_direct_xai_call_kwargs(fake_llm):
    fake_llm.queue({"message": "hello", "trades": [], "watchlist_changes": []})
    r = await client.request_structured_response(MSGS)
    assert r.message == "hello"
    call = fake_llm.calls[0]
    assert call["model"] == "xai/grok-4.7"
    assert call["api_key"] == "test-key"
    assert "api_base" not in call
    assert call["response_format"] == RESPONSE_FORMAT
    assert call["messages"] == MSGS


async def test_proxy_call_kwargs(fake_llm, monkeypatch):
    monkeypatch.setenv("LITELLM_API_BASE", "http://proxy:4000")
    monkeypatch.setenv("LLM_MODEL", "xai/grok-4")
    fake_llm.queue({"message": "hello"})
    await client.request_structured_response(MSGS)
    call = fake_llm.calls[0]
    assert call["model"] == "litellm_proxy/xai/grok-4"
    assert call["api_base"] == "http://proxy:4000"


async def test_empty_env_vars_mean_unset(fake_llm, monkeypatch):
    # Docker --env-file passes `LITELLM_API_BASE=` as "", which must act like unset.
    monkeypatch.setenv("LITELLM_API_BASE", "")
    monkeypatch.setenv("LLM_MODEL", "  ")
    fake_llm.queue({"message": "hello"})
    await client.request_structured_response(MSGS)
    call = fake_llm.calls[0]
    assert call["model"] == "xai/grok-4.7"
    assert "api_base" not in call


@pytest.mark.parametrize("api_key,expected", [
    ("virtual-key", "virtual-key"),   # LITELLM_API_KEY wins for proxy calls
    ("", "test-key"),                 # empty -> falls back to LITELLM_MASTER_KEY
    (None, "test-key"),               # unset -> falls back too
])
async def test_proxy_key_precedence(fake_llm, monkeypatch, api_key, expected):
    monkeypatch.setenv("LITELLM_API_BASE", "http://proxy:4000")
    if api_key is not None:
        monkeypatch.setenv("LITELLM_API_KEY", api_key)
    fake_llm.queue({"message": "hello"})
    await client.request_structured_response(MSGS)
    assert fake_llm.calls[0]["api_key"] == expected


async def test_direct_mode_ignores_proxy_virtual_key(fake_llm, monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "virtual-key")
    fake_llm.queue({"message": "hello"})
    await client.request_structured_response(MSGS)
    assert fake_llm.calls[0]["api_key"] == "test-key"


async def test_no_key_omits_api_key(fake_llm, monkeypatch):
    monkeypatch.delenv("LITELLM_MASTER_KEY")
    fake_llm.queue({"message": "hello"})
    await client.request_structured_response(MSGS)
    assert "api_key" not in fake_llm.calls[0]


async def test_retry_once_then_succeeds(fake_llm):
    fake_llm.queue("garbage", {"message": "second try"})
    r = await client.request_structured_response(MSGS)
    assert r.message == "second try"
    assert len(fake_llm.calls) == 2


async def test_two_bad_outputs_give_fallback(fake_llm):
    fake_llm.queue("garbage", {"message": 5})
    r = await client.request_structured_response(MSGS)
    assert r.message == client.FALLBACK_MESSAGE
    assert r.trades == [] and r.watchlist_changes == []
    assert len(fake_llm.calls) == 2


async def test_call_failure_raises(fake_llm):
    fake_llm.queue(ConnectionError("boom"))
    with pytest.raises(client.LLMUnavailableError):
        await client.request_structured_response(MSGS)


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("TRUE", True), ("1", True), ("false", False), ("", False),
])
def test_mock_mode_flag(monkeypatch, value, expected):
    monkeypatch.setenv("LLM_MOCK", value)
    assert client.is_mock_mode() is expected
