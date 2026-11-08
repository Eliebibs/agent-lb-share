"""Exercise the actual subscription route after real usage persistence."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select, update

from app.core.clients.anthropic_usage import AnthropicOAuthUsagePayload, _usage_payload_from_anthropic
from app.core.utils.time import utcnow
from app.db.models import Account, UsageHistory
from app.db.session import SessionLocal
from app.modules.oauth.subscription_usage import refresh_subscription_login_usage
from app.modules.proxy.anthropic_service import AnthropicProxyService
from app.modules.usage.repository import UsageRepository
from app.modules.usage.subscription_billing import SUBSCRIPTION_BILLING_WINDOW
from tests.integration.test_subscription_proxy import _BODY, _account, _Response

pytestmark = pytest.mark.integration


async def _refresh(monkeypatch, flags, *, weekly=True):
    import app.modules.usage.updater as usage_module

    async def fetch_usage(**kwargs):
        return _usage_payload_from_anthropic(AnthropicOAuthUsagePayload.model_validate({
            "five_hour": None,
            "seven_day": {"utilization": 26} if weekly else None,
            "extra_usage": flags,
        }))

    monkeypatch.setattr(usage_module, "fetch_anthropic_usage", fetch_usage)
    return await refresh_subscription_login_usage("billing")


@pytest.mark.asyncio
@pytest.mark.parametrize("weekly", [True, False])
async def test_null_primary_preserves_quota_and_authorizes_fresh_billing(async_client, monkeypatch, weekly):
    await _account("billing", age=3600)
    async with SessionLocal() as session:
        await session.execute(delete(UsageHistory).where(UsageHistory.account_id == "billing"))
        await session.commit()
    assert await _refresh(monkeypatch, {"is_enabled": False}, weekly=weekly) == "ready"
    monkeypatch.setattr(AnthropicProxyService, "_open_upstream_response", lambda *a, **kw: _Response())
    response = await async_client.post("/subscription/v1/messages", json=_BODY)
    assert response.status_code == 200
    async with SessionLocal() as session:
        repo = UsageRepository(session)
        assert await repo.latest_entry_for_account("billing", window="primary") is None
        secondary = await repo.latest_entry_for_account("billing", window="secondary")
        aggregate = await repo.aggregate_since(utcnow() - timedelta(hours=1))
        trends = await repo.trends_by_bucket(utcnow() - timedelta(hours=1))
        if weekly:
            assert secondary.used_percent == 26
            assert aggregate[0].used_percent_avg == 26
            assert aggregate[0].samples == 1
            assert len(trends) == 1
        else:
            assert secondary is None
            assert aggregate == []
            assert trends == []


@pytest.mark.asyncio
@pytest.mark.parametrize("weekly", [True, False])
@pytest.mark.parametrize("flags,status", [
    ({"is_enabled": True}, "extra_usage_enabled"),
    (None, "usage_unverified"),
    ({}, "usage_unverified"),
])
async def test_new_unknown_or_enabled_evidence_overrides_disabled(
    async_client, monkeypatch, weekly, flags, status,
):
    await _account("billing")
    assert await _refresh(monkeypatch, {"is_enabled": False}) == "ready"
    assert await _refresh(monkeypatch, flags, weekly=weekly) == status

    def forbidden(*a, **kw):
        pytest.fail("Unsafe billing reached inference")

    monkeypatch.setattr(AnthropicProxyService, "_open_upstream_response", forbidden)
    assert (await async_client.post("/subscription/v1/messages", json=_BODY)).status_code == 403


@pytest.mark.asyncio
async def test_billing_observation_preserves_five_minute_refresh_cadence(async_client, monkeypatch):
    import app.modules.usage.updater as usage_module

    await _account("billing", age=3600)
    assert await _refresh(monkeypatch, {"is_enabled": False}) == "ready"
    monkeypatch.setattr(usage_module, "get_settings", lambda: SimpleNamespace(
        usage_refresh_enabled=True, anthropic_usage_refresh_interval_seconds=300,
    ))
    async with SessionLocal() as session:
        account = await session.get(Account, "billing")
        repo = UsageRepository(session)
        updater = usage_module.UsageUpdater(repo)
        fetch = AsyncMock(return_value=usage_module.AccountRefreshResult(usage_written=False))
        monkeypatch.setattr(updater, "_refresh_account", fetch)
        for age, expected_calls in [(299, 0), (301, 1)]:
            await session.execute(update(UsageHistory).where(
                UsageHistory.window == SUBSCRIPTION_BILLING_WINDOW,
            ).values(recorded_at=utcnow() - timedelta(seconds=age)))
            await session.commit()
            await updater.refresh_accounts([account], await repo.latest_by_account(window="primary"))
            assert fetch.await_count == expected_calls


@pytest.mark.asyncio
async def test_stale_marker_does_not_fall_back_to_fresh_legacy_primary(async_client, monkeypatch):
    await _account("billing")
    assert await _refresh(monkeypatch, {"is_enabled": False}) == "ready"
    async with SessionLocal() as session:
        await session.execute(update(UsageHistory).where(
            UsageHistory.window == SUBSCRIPTION_BILLING_WINDOW,
        ).values(recorded_at=utcnow() - timedelta(seconds=901)))
        await session.commit()
    assert (await async_client.post("/subscription/v1/messages", json=_BODY)).status_code == 403


@pytest.mark.asyncio
async def test_failed_refresh_does_not_create_evidence_or_report_ready(async_client, monkeypatch):
    import app.modules.usage.updater as usage_module
    from app.core.clients.usage import UsageFetchError

    await _account("billing", age=3600)

    async def fail(**kwargs):
        raise UsageFetchError(429, "Usage temporarily throttled")

    monkeypatch.setattr(usage_module, "fetch_anthropic_usage", fail)
    assert await refresh_subscription_login_usage("billing") == "usage_unverified"
    async with SessionLocal() as session:
        rows = await session.scalars(select(UsageHistory).where(UsageHistory.window == SUBSCRIPTION_BILLING_WINDOW))
        assert list(rows) == []
    assert (await async_client.post("/subscription/v1/messages", json=_BODY)).status_code == 403
