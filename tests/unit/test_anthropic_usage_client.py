from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.clients import anthropic_usage as anthropic_usage_module
from app.core.clients.anthropic_usage import (
    AnthropicOAuthUsagePayload,
    _usage_payload_from_anthropic,
    fetch_anthropic_usage,
)
from app.core.clients.usage import UsageFetchError

pytestmark = pytest.mark.unit


class _StubResponse:
    def __init__(self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._payload = payload
        self.headers = headers or {}

    async def json(self, content_type: str | None = None) -> dict[str, Any]:
        return self._payload

    async def text(self) -> str:
        return ""


class _StubRequestContext:
    def __init__(self, client: "_StubRetryClient", retry_options: object) -> None:
        self._client = client
        self._retry_options = retry_options

    async def __aenter__(self) -> _StubResponse:
        attempts = getattr(self._retry_options, "attempts", 1)
        statuses = set(getattr(self._retry_options, "statuses", set()))
        response = self._client.responses[-1]
        for attempt in range(attempts):
            index = min(self._client.calls, len(self._client.responses) - 1)
            response = self._client.responses[index]
            self._client.calls += 1
            if response.status not in statuses or attempt == attempts - 1:
                return response
        return response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _StubRetryClient:
    def __init__(self, responses: list[_StubResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def request(self, *args: object, retry_options: object, **kwargs: object) -> _StubRequestContext:
        return _StubRequestContext(self, retry_options)


@pytest.fixture(autouse=True)
def _clear_usage_cooldowns():
    anthropic_usage_module._usage_rate_limit_cooldowns.clear()
    yield
    anthropic_usage_module._usage_rate_limit_cooldowns.clear()


def _install_retry_client(monkeypatch: pytest.MonkeyPatch, client: _StubRetryClient) -> None:
    @asynccontextmanager
    async def lease():
        yield client

    monkeypatch.setattr(anthropic_usage_module, "lease_retry_client", lease)


def test_anthropic_usage_maps_top_model_session_and_week_windows_only() -> None:
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "five_hour": {
                "utilization": 100.0,
                "resets_at": "2026-06-09T19:50:00.000000+00:00",
            },
            "seven_day": {
                "utilization": 21.0,
                "resets_at": "2026-06-14T21:00:00.000000+00:00",
            },
            "seven_day_sonnet": {
                "utilization": 1.0,
                "resets_at": "2026-06-14T21:00:00.000000+00:00",
            },
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.plan_type == "claude"
    assert usage.rate_limit is not None
    assert usage.rate_limit.primary_window is not None
    assert usage.rate_limit.primary_window.used_percent == 100.0
    assert usage.rate_limit.primary_window.limit_window_seconds == 5 * 60 * 60
    assert usage.rate_limit.primary_window.reset_at == 1781034600
    assert usage.rate_limit.secondary_window is not None
    assert usage.rate_limit.secondary_window.used_percent == 21.0
    assert usage.rate_limit.secondary_window.limit_window_seconds == 7 * 24 * 60 * 60
    assert usage.rate_limit.secondary_window.reset_at == 1781470800
    assert usage.credits is None


def test_anthropic_usage_maps_enabled_extra_usage_to_credits() -> None:
    # Shape observed live on /api/oauth/usage (2026-07-01): monthly_limit and
    # used_credits arrive in currency minor units scaled by decimal_places.
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "five_hour": {"utilization": 100.0, "resets_at": "2026-07-02T02:10:00+00:00"},
            "seven_day": {"utilization": 20.0, "resets_at": "2026-07-05T21:00:00+00:00"},
            "extra_usage": {
                "is_enabled": True,
                "monthly_limit": 25000,
                "used_credits": 6340.0,
                "utilization": 25.36,
                "currency": "USD",
                "decimal_places": 2,
                "disabled_reason": None,
            },
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.credits is not None
    assert usage.credits.has_credits is True
    assert usage.credits.unlimited is False
    assert usage.credits.balance is not None
    assert float(usage.credits.balance) == pytest.approx(186.60)


def test_anthropic_usage_maps_disabled_extra_usage_without_fabricated_balance() -> None:
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "five_hour": {"utilization": 10.0, "resets_at": "2026-07-02T02:10:00+00:00"},
            "extra_usage": {
                "is_enabled": False,
                "monthly_limit": None,
                "used_credits": None,
                "utilization": None,
                "currency": None,
                "decimal_places": None,
            },
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.credits is not None
    assert usage.credits.has_credits is False
    assert usage.credits.unlimited is False
    assert usage.credits.balance is None


@pytest.mark.parametrize("extra_usage", [{}, {"is_enabled": None}])
def test_unknown_extra_usage_does_not_claim_billing_is_disabled(extra_usage) -> None:
    payload = AnthropicOAuthUsagePayload.model_validate({"extra_usage": extra_usage})
    assert _usage_payload_from_anthropic(payload).credits is None


def test_anthropic_usage_extracts_fable_scoped_weekly_limit() -> None:
    # Real captured api/oauth/usage payload shape (2026-07-02).
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "five_hour": {"utilization": 10.0, "resets_at": "2026-07-02T02:10:00+00:00"},
            "seven_day": {"utilization": 62.0, "resets_at": "2026-07-05T21:00:00.091084+00:00"},
            "limits": [
                {
                    "kind": "session",
                    "group": "session",
                    "percent": 52,
                    "severity": "normal",
                    "resets_at": "2026-07-02T18:09:59.091065+00:00",
                    "scope": None,
                    "is_active": False,
                },
                {
                    "kind": "weekly_all",
                    "group": "weekly",
                    "percent": 62,
                    "severity": "normal",
                    "resets_at": "2026-07-05T21:00:00.091084+00:00",
                    "scope": None,
                    "is_active": False,
                },
                {
                    "kind": "weekly_scoped",
                    "group": "weekly",
                    "percent": 81,
                    "severity": "warning",
                    "resets_at": "2026-07-05T21:00:00.091366+00:00",
                    "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
                    "is_active": True,
                },
            ],
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.fable_scoped_weekly is not None
    assert usage.fable_scoped_weekly.used_percent == 81.0
    assert usage.fable_scoped_weekly.limit_window_seconds == 7 * 24 * 60 * 60
    assert usage.fable_scoped_weekly.reset_at == int(
        datetime.fromisoformat("2026-07-05T21:00:00.091366+00:00").timestamp()
    )


def test_anthropic_usage_absent_limits_yields_no_fable_scoped_weekly() -> None:
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "five_hour": {"utilization": 10.0, "resets_at": "2026-07-02T02:10:00+00:00"},
            "seven_day": {"utilization": 20.0, "resets_at": "2026-07-05T21:00:00+00:00"},
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.fable_scoped_weekly is None


def test_anthropic_usage_ignores_weekly_scoped_entry_for_non_fable_model() -> None:
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "seven_day": {"utilization": 20.0, "resets_at": "2026-07-05T21:00:00+00:00"},
            "limits": [
                {
                    "kind": "weekly_scoped",
                    "group": "weekly",
                    "percent": 90,
                    "severity": "warning",
                    "resets_at": "2026-07-05T21:00:00+00:00",
                    "scope": {"model": {"id": None, "display_name": "Sonnet"}, "surface": None},
                    "is_active": True,
                },
            ],
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.fable_scoped_weekly is None


def test_anthropic_usage_matches_fable_scope_case_insensitively() -> None:
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "seven_day": {"utilization": 20.0, "resets_at": "2026-07-05T21:00:00+00:00"},
            "limits": [
                {
                    "kind": "weekly_scoped",
                    "group": "weekly",
                    "percent": 45,
                    "severity": "normal",
                    "resets_at": "2026-07-05T21:00:00+00:00",
                    "scope": {"model": {"id": None, "display_name": "fABLE"}, "surface": None},
                    "is_active": True,
                },
            ],
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.fable_scoped_weekly is not None
    assert usage.fable_scoped_weekly.used_percent == 45.0


def test_anthropic_usage_extra_usage_balance_never_goes_negative() -> None:
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "five_hour": {"utilization": 100.0, "resets_at": "2026-07-02T02:10:00+00:00"},
            "extra_usage": {
                "is_enabled": True,
                "monthly_limit": 1000,
                "used_credits": 1500.0,
                "utilization": 150.0,
                "currency": "USD",
                "decimal_places": 2,
            },
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.credits is not None
    assert usage.credits.has_credits is True
    assert usage.credits.balance is not None
    assert float(usage.credits.balance) == 0.0


@pytest.mark.asyncio
async def test_anthropic_usage_429_is_not_retried_and_cools_same_credential(monkeypatch) -> None:
    client = _StubRetryClient(
        [_StubResponse(429, {"error": {"message": "Rate limited"}}, {"Retry-After": "120"})]
    )
    _install_retry_client(monkeypatch, client)

    with pytest.raises(UsageFetchError) as first:
        await fetch_anthropic_usage(access_token="old-token", max_retries=2)
    with pytest.raises(UsageFetchError) as cooled:
        await fetch_anthropic_usage(access_token="old-token", max_retries=2)

    assert first.value.status_code == 429
    assert cooled.value.status_code == 429
    assert client.calls == 1


@pytest.mark.asyncio
async def test_anthropic_usage_rotated_credential_does_not_inherit_cooldown(monkeypatch) -> None:
    client = _StubRetryClient(
        [
            _StubResponse(429, {"error": {"message": "Rate limited"}}),
            _StubResponse(
                200,
                {
                    "five_hour": {"utilization": 1.0},
                    "extra_usage": {"is_enabled": False},
                },
            ),
        ]
    )
    _install_retry_client(monkeypatch, client)

    with pytest.raises(UsageFetchError):
        await fetch_anthropic_usage(access_token="old-token", max_retries=2)
    payload = await fetch_anthropic_usage(access_token="rotated-token", max_retries=2)

    assert client.calls == 2
    assert payload.credits is not None
    assert payload.credits.has_credits is False


@pytest.mark.asyncio
async def test_anthropic_usage_retries_same_credential_after_cooldown_expires(monkeypatch) -> None:
    now = 100.0
    monkeypatch.setattr(anthropic_usage_module.time, "monotonic", lambda: now)
    client = _StubRetryClient(
        [
            _StubResponse(429, {"error": {"message": "Rate limited"}}, {"Retry-After": "60"}),
            _StubResponse(200, {"five_hour": {"utilization": 4.0}}),
        ]
    )
    _install_retry_client(monkeypatch, client)

    with pytest.raises(UsageFetchError):
        await fetch_anthropic_usage(access_token="expiring-token", max_retries=2)
    now = 161.0
    payload = await fetch_anthropic_usage(access_token="expiring-token", max_retries=2)

    assert client.calls == 2
    assert payload.rate_limit is not None
    assert payload.rate_limit.primary_window is not None
    assert payload.rate_limit.primary_window.used_percent == 4.0


@pytest.mark.asyncio
async def test_anthropic_usage_preserves_non_429_retries(monkeypatch) -> None:
    client = _StubRetryClient(
        [
            _StubResponse(503, {"error": {"message": "Busy"}}),
            _StubResponse(200, {"five_hour": {"utilization": 2.0}}),
        ]
    )
    _install_retry_client(monkeypatch, client)

    payload = await fetch_anthropic_usage(access_token="retry-token", max_retries=1)

    assert client.calls == 2
    assert payload.rate_limit is not None
    assert payload.rate_limit.primary_window is not None
    assert payload.rate_limit.primary_window.used_percent == 2.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("45", 45.0),
        ("Wed, 30 Sep 2026 19:05:00 GMT", 300.0),
        ("Wed, 30 Sep 2026 18:55:00 GMT", 300.0),
        ("0", 300.0),
        ("nan", 300.0),
        ("inf", 300.0),
        ("-1", 300.0),
        ("not-a-date", 300.0),
        (None, 300.0),
    ],
)
def test_anthropic_usage_retry_after_seconds(value, expected) -> None:
    now = datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc)

    assert anthropic_usage_module._retry_after_seconds(value, now=now) == expected


def test_anthropic_usage_cooldown_prunes_expired_rotated_credentials(monkeypatch) -> None:
    monkeypatch.setattr(anthropic_usage_module.time, "monotonic", lambda: 100.0)
    anthropic_usage_module._usage_rate_limit_cooldowns.update({"expired": 99.0, "active": 101.0})

    anthropic_usage_module._prune_usage_rate_limit_cooldowns()

    assert anthropic_usage_module._usage_rate_limit_cooldowns == {"active": 101.0}


@pytest.mark.asyncio
async def test_successful_anthropic_usage_response_clears_existing_cooldown() -> None:
    credential_key = anthropic_usage_module._credential_key("success-token")
    anthropic_usage_module._usage_rate_limit_cooldowns[credential_key] = float("inf")
    response = _StubResponse(200, {"five_hour": {"utilization": 3.0}})

    await anthropic_usage_module._usage_payload_or_raise(response, credential_key=credential_key)

    assert credential_key not in anthropic_usage_module._usage_rate_limit_cooldowns
