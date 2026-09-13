import json
import os
from decimal import Decimal

import httpx
import pytest

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderError,
    ProviderRefusedError,
    ProviderResponseError,
    ProviderTransportError,
)
from framemeld.providers.gemini import GeminiLLMProvider


def _provider(handler, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    return GeminiLLMProvider(transport=httpx.MockTransport(handler))


def _ok(text, finish_reason="STOP", **extra):
    body = {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish_reason}]
    } | extra
    return httpx.Response(200, json=body)


async def test_a_successful_call_returns_the_content(monkeypatch):
    provider = _provider(lambda request: _ok('{"scenes":[]}'), monkeypatch)
    result = await provider.complete(system="s", user="u", json_schema={"name": "x"})
    assert result.text == '{"scenes":[]}'
    assert result.provider == "gemini"


async def test_the_request_carries_the_key_and_the_schema(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key_header"] = request.headers["x-goog-api-key"]
        seen["body"] = json.loads(request.content)
        return _ok("{}")

    await _provider(handler, monkeypatch).complete(
        system="sys", user="usr", json_schema={"name": "script", "schema": {"type": "object"}}
    )
    assert seen["key_header"] == "test-key"
    assert seen["body"]["generationConfig"]["responseSchema"] == {"type": "object"}
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert seen["body"]["contents"][0]["parts"][0]["text"] == "usr"


async def test_a_missing_schema_body_omits_responseschema(monkeypatch):
    """json_schema={} is what the client-error and network-failure tests
    below pass -- the adapter must not crash building the request just
    because there is no schema to forward."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _ok("{}")

    await _provider(handler, monkeypatch).complete(system="s", user="u", json_schema={})
    assert "responseSchema" not in seen["body"]["generationConfig"]


async def test_cost_is_zero_pending_a_real_token_price_table(monkeypatch):
    """Gemini's usageMetadata reports tokens, never a dollar figure the way
    OpenRouter's usage.cost does -- see the adapter's comment. M2 has no
    credit ledger to feed regardless."""
    provider = _provider(
        lambda request: _ok(
            "{}", usageMetadata={"promptTokenCount": 100, "candidatesTokenCount": 50}
        ),
        monkeypatch,
    )
    result = await provider.complete(system="s", user="u", json_schema={})
    assert result.cost_usd == Decimal("0")


@pytest.mark.parametrize("status_code", [408, 409, 425, 429, 500])
async def test_transient_statuses_are_retryable(status_code, monkeypatch):
    provider = _provider(lambda request: httpx.Response(status_code, json={}), monkeypatch)
    with pytest.raises(ProviderTransportError):
        await provider.complete(system="s", user="u", json_schema={})


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
async def test_client_errors_are_terminal(status_code, monkeypatch):
    provider = _provider(lambda request: httpx.Response(status_code, json={}), monkeypatch)
    with pytest.raises(ProviderResponseError) as caught:
        await provider.complete(system="s", user="u", json_schema={})
    assert caught.value.retryable is False


async def test_a_network_failure_is_retryable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("no route to host")

    with pytest.raises(ProviderTransportError):
        await _provider(handler, monkeypatch).complete(system="s", user="u", json_schema={})


async def test_a_blocked_prompt_with_no_candidates_is_a_refusal(monkeypatch):
    body = {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
    provider = _provider(lambda request: httpx.Response(200, json=body), monkeypatch)
    with pytest.raises(ProviderRefusedError):
        await provider.complete(system="s", user="u", json_schema={})


async def test_no_candidates_and_no_block_reason_is_a_response_error(monkeypatch):
    """Distinguishes an honest empty reply from a blocked one -- the
    message a user sees should not claim a refusal that never happened."""
    provider = _provider(lambda request: httpx.Response(200, json={"candidates": []}), monkeypatch)
    with pytest.raises(ProviderResponseError):
        await provider.complete(system="s", user="u", json_schema={})


@pytest.mark.parametrize("finish_reason", ["SAFETY", "RECITATION", "PROHIBITED_CONTENT"])
async def test_a_safety_finish_reason_is_a_refusal_not_a_retry(finish_reason, monkeypatch):
    provider = _provider(lambda request: _ok("", finish_reason=finish_reason), monkeypatch)
    with pytest.raises(ProviderRefusedError):
        await provider.complete(system="s", user="u", json_schema={})


async def test_a_truncated_response_names_the_length_limit(monkeypatch):
    provider = _provider(lambda request: _ok("{", finish_reason="MAX_TOKENS"), monkeypatch)
    with pytest.raises(ProviderResponseError) as caught:
        await provider.complete(system="s", user="u", json_schema={})
    assert caught.value.retryable is False
    message = str(caught.value).lower()
    assert "shorter" in message or "length" in message


async def test_empty_parts_is_a_response_error(monkeypatch):
    body = {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}
    provider = _provider(lambda request: httpx.Response(200, json=body), monkeypatch)
    with pytest.raises(ProviderResponseError):
        await provider.complete(system="s", user="u", json_schema={})


def _raise_connect_error_quoting_the_key(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("failed to reach host with test-key")


@pytest.mark.parametrize(
    ("name", "handler"),
    [
        ("client error", lambda request: httpx.Response(401, text="bad key test-key")),
        ("server error", lambda request: httpx.Response(500, text="upstream said test-key")),
        ("network failure", _raise_connect_error_quoting_the_key),
    ],
)
async def test_no_error_path_quotes_the_api_key(name, handler, monkeypatch):
    provider = _provider(handler, monkeypatch)
    with pytest.raises(ProviderError) as caught:
        await provider.complete(system="s", user="u", json_schema={})
    assert "test-key" not in str(caught.value)


@pytest.mark.skipif(
    not os.getenv("FRAMEMELD_LIVE_LLM"),
    reason="set FRAMEMELD_LIVE_LLM=1 to call the real Gemini API",
)
async def test_the_real_api_answers():
    """Run once by hand after adding the key:
    FRAMEMELD_LIVE_LLM=1 pytest tests/test_providers_gemini.py -k real -v

    Deliberately not part of the default suite, same reasoning as the
    OpenRouter equivalent: a live call in CI would be flaky and would
    burn free-tier quota for no repeatable signal.
    """
    from framemeld.script.generation import SCRIPT_JSON_SCHEMA, build_messages

    assert settings.gemini_api_key, "GEMINI_API_KEY is not set"
    system, user = build_messages("Elena found a letter. She burned it.")
    result = await GeminiLLMProvider().complete(
        system=system, user=user, json_schema=SCRIPT_JSON_SCHEMA
    )
    assert json.loads(result.text)["scenes"]
