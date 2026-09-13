import json
import os

import httpx
import pytest

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderTransportError,
)
from framemeld.providers.openrouter import OpenRouterLLMProvider


def _provider(handler, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-test-key")
    return OpenRouterLLMProvider(transport=httpx.MockTransport(handler))


def _ok(content, **extra):
    body = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]} | extra
    return httpx.Response(200, json=body)


async def test_a_successful_call_returns_the_content(monkeypatch):
    provider = _provider(lambda request: _ok('{"scenes":[]}'), monkeypatch)
    result = await provider.complete(system="s", user="u", json_schema={"name": "x"})
    assert result.text == '{"scenes":[]}'
    assert result.provider == "openrouter"


async def test_the_request_carries_the_key_and_the_schema(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return _ok("{}")

    await _provider(handler, monkeypatch).complete(
        system="sys", user="usr", json_schema={"name": "script"}
    )
    assert seen["auth"] == "Bearer sk-test-key"
    assert seen["body"]["response_format"]["json_schema"]["name"] == "script"
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]


async def test_reported_cost_is_used_when_present(monkeypatch):
    from decimal import Decimal

    provider = _provider(lambda request: _ok("{}", usage={"cost": 0.00042}), monkeypatch)
    result = await provider.complete(system="s", user="u", json_schema={})
    assert result.cost_usd == Decimal("0.00042")


async def test_a_free_model_reporting_no_cost_is_zero_not_an_error(monkeypatch):
    from decimal import Decimal

    provider = _provider(lambda request: _ok("{}"), monkeypatch)
    result = await provider.complete(system="s", user="u", json_schema={})
    assert result.cost_usd == Decimal("0")


@pytest.mark.parametrize("status_code", [429, 500, 502, 503])
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


async def test_a_content_filter_is_a_refusal_not_a_retry(monkeypatch):
    body = {"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]}
    provider = _provider(lambda request: httpx.Response(200, json=body), monkeypatch)
    with pytest.raises(ProviderRefusedError):
        await provider.complete(system="s", user="u", json_schema={})


async def test_an_empty_choices_list_is_a_response_error(monkeypatch):
    provider = _provider(lambda request: httpx.Response(200, json={"choices": []}), monkeypatch)
    with pytest.raises(ProviderResponseError):
        await provider.complete(system="s", user="u", json_schema={})


async def test_errors_never_quote_the_api_key(monkeypatch):
    """An adapter error ends up in jobs.error, which is shown to the user."""
    provider = _provider(
        lambda request: httpx.Response(401, text="bad key sk-test-key"), monkeypatch
    )
    with pytest.raises(ProviderResponseError) as caught:
        await provider.complete(system="s", user="u", json_schema={})
    assert "sk-test-key" not in str(caught.value)


@pytest.mark.skipif(
    not os.getenv("FRAMEMELD_LIVE_LLM"),
    reason="set FRAMEMELD_LIVE_LLM=1 to call the real OpenRouter API",
)
async def test_the_real_api_answers():
    """Run once by hand after adding the key:
    FRAMEMELD_LIVE_LLM=1 pytest tests/test_providers_openrouter.py -k real -v

    Deliberately not part of the default suite: free models are capped at
    roughly 200 requests a day and the roster changes without notice, so a
    live call in CI would be a flaky test that also burns the budget.
    """
    from framemeld.script.generation import SCRIPT_JSON_SCHEMA, build_messages

    assert settings.openrouter_api_key, "OPENROUTER_API_KEY is not set"
    system, user = build_messages("Elena found a letter. She burned it.")
    result = await OpenRouterLLMProvider().complete(
        system=system, user=user, json_schema=SCRIPT_JSON_SCHEMA
    )
    assert json.loads(result.text)["scenes"]
