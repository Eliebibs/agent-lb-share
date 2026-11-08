from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.core.auth.refresh import RefreshError
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.auth_manager import AuthManager
from app.modules.proxy.anthropic_service import AnthropicProxyService

pytestmark = pytest.mark.integration
_BODY = {"model": "claude-haiku-4-5", "max_tokens": 4, "messages": [{"role": "user", "content": "ok"}]}
_SUCCESS = b'{"id":"msg_test","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":1,"output_tokens":1}}'


class _Response:
    def __init__(self, status: int = 200, body: bytes = _SUCCESS, headers: dict[str, str] | None = None):
        self.status = status
        self.body = body
        self.headers = {"content-type": "application/json", **(headers or {})}
        self.content = self

    async def read(self) -> bytes:
        return self.body

    async def iter_chunked(self, _: int) -> AsyncIterator[bytes]:
        yield self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


async def _account(
    name: str, *, credits: bool | None = False, age: int = 0, oauth: bool = True,
    exhausted: bool = False, status: AccountStatus = AccountStatus.ACTIVE,
) -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(Account(
            id=name, provider="anthropic", email=f"{name}@example.com", plan_type="max",
            access_token_encrypted=encryptor.encrypt(f"sk-ant-oat01-{name}" if oauth else f"sk-ant-api03-{name}"),
            refresh_token_encrypted=encryptor.encrypt(f"sk-ant-ort01-{name}" if oauth else "api-key"),
            last_refresh=utcnow() + timedelta(days=1), status=status,
        ))
        await session.flush()
        session.add(UsageHistory(
            account_id=name, provider="anthropic", window="primary",
            used_percent=100 if exhausted else 0,
            reset_at=int((utcnow() + timedelta(hours=1)).timestamp()),
            recorded_at=utcnow() - timedelta(seconds=age),
            credits_has=credits, credits_unlimited=False if credits is not None else None,
            credits_balance=100 if credits else None,
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_policy_and_unknown_paths_fail_closed(async_client):
    response = await async_client.get("/subscription/policy")
    assert response.json() == {
        "provider": "anthropic", "authentication": "subscription-oauth",
        "apiKeyFallback": False, "extraUsage": False,
    }
    response = await async_client.post("/subscription/v1/unknown", json=_BODY)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_subscription_policy_does_not_bypass_remote_proxy_auth(app_instance):
    from httpx import ASGITransport, AsyncClient

    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("203.0.113.10", 4242))
        async with AsyncClient(transport=transport, base_url="http://agent.example") as client:
            response = await client.get(
                "/subscription/policy", headers={"Authorization": "Bearer sagecontrol-subscription-only"},
            )
            assert response.status_code == 401


@pytest.mark.parametrize("age,allowed", [(899, True), (900, True), (901, False), (-1, False)])
def test_billing_snapshot_exact_age_boundary(monkeypatch, age, allowed):
    import app.modules.proxy.subscription_policy as policy

    encryptor = TokenEncryptor()
    account = Account(
        id="boundary", provider="anthropic", access_token_encrypted=encryptor.encrypt("sk-ant-oat01-test"),
        refresh_token_encrypted=encryptor.encrypt("sk-ant-ort01-test"),
    )
    snapshot = UsageHistory(
        account_id=account.id, used_percent=0, credits_has=False, credits_unlimited=False,
        recorded_at=datetime.fromtimestamp(10000 - age, timezone.utc).replace(tzinfo=None),
    )
    monkeypatch.setattr(policy, "time", SimpleNamespace(time=lambda: 10000))
    assert bool(policy.subscription_eligible_ids(
        [account], [account.id], {account.id: snapshot}, encryptor, require_disabled_extra_usage=True,
    )) is allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["glm-5.2", "ccgpt", "gpt-5"])
async def test_non_claude_models_rejected(async_client, model):
    for route in ("messages", "messages/count_tokens"):
        response = await async_client.post(f"/subscription/v1/{route}", json={**_BODY, "model": model})
        assert response.status_code == 400
        assert response.json()["error"]["type"] == "subscription_model_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"credits": True}, {"credits": None}, {"age": 901}, {"age": -60}, {"oauth": False},
    {"status": AccountStatus.REAUTH_REQUIRED},
])
async def test_unsafe_account_never_dispatched(async_client, monkeypatch, options):
    await _account("unsafe", **options)

    def forbidden(*args, **kwargs):
        pytest.fail("Unsafe credentials reached inference")

    monkeypatch.setattr(AnthropicProxyService, "_open_upstream_response", forbidden)
    response = await async_client.post("/subscription/v1/messages", json=_BODY)
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "subscription_login_required"


@pytest.mark.asyncio
async def test_subscription_dispatch_replaces_sentinel_and_strips_api_key(async_client, monkeypatch):
    await _account("safe")
    captured = []

    def upstream(self, session, *, provider_name, headers, json_body):
        captured.append((provider_name, headers))
        return _Response()

    monkeypatch.setattr(AnthropicProxyService, "_open_upstream_response", upstream)
    response = await async_client.post(
        "/subscription/v1/messages", json=_BODY,
        headers={"Authorization": "Bearer sagecontrol-subscription-only", "x-api-key": "must-not-forward"},
    )
    assert response.status_code == 200
    assert captured[0][0] == "anthropic"
    assert captured[0][1]["Authorization"] == "Bearer sk-ant-oat01-safe"
    assert "x-api-key" not in {key.lower() for key in captured[0][1]}


@pytest.mark.asyncio
async def test_global_paid_fallback_cannot_override_subscription_route(async_client, monkeypatch):
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    await _account("exhausted", credits=True, exhausted=True)

    def forbidden(*args, **kwargs):
        pytest.fail("Paid fallback dispatched inference")

    monkeypatch.setattr(AnthropicProxyService, "_open_upstream_response", forbidden)
    response = await async_client.post("/subscription/v1/messages", json=_BODY)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_model_override_cannot_route_to_other_provider(async_client, monkeypatch):
    from app.core.config.settings import Settings

    monkeypatch.setattr(Settings, "resolve_anthropic_model_override", lambda self, model: "glm-5.2")
    response = await async_client.post("/subscription/v1/messages", json=_BODY)
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["messages", "messages/count_tokens"])
@pytest.mark.parametrize("failure", ["refresh", "401", "403"])
async def test_auth_failure_rotates_before_response(async_client, monkeypatch, route, failure):
    await _account("first")
    await _account("second")
    calls = []
    refreshes = []

    async def refresh(self, account, **kwargs):
        refreshes.append(account.id)
        if len(refreshes) == 1 and failure == "refresh":
            raise RefreshError("auth_refresh_invalid_grant", "Re-login required", True)
        return account

    def upstream(self, session, *, provider_name, headers, json_body):
        calls.append(headers["Authorization"])
        if len(calls) == 1 and failure != "refresh":
            return _Response(int(failure), b'{"error":{"message":"Please sign in"}}')
        return _Response(body=b'{"input_tokens":1}' if route.endswith("count_tokens") else _SUCCESS)

    monkeypatch.setattr(AuthManager, "ensure_fresh", refresh)
    monkeypatch.setattr(AnthropicProxyService, "_open_upstream_response", upstream)
    monkeypatch.setattr(AnthropicProxyService, "_open_count_tokens_response", upstream)
    response = await async_client.post(f"/subscription/v1/{route}", json=_BODY)
    assert response.status_code == 200
    assert len(refreshes) == 2
    assert refreshes[0] != refreshes[1]
    if route.endswith("count_tokens"):
        async with SessionLocal() as session:
            assert list((await session.scalars(select(RequestLog))).all()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["messages", "messages/count_tokens"])
async def test_all_auth_rejections_surface_immediately_with_login_hint(async_client, monkeypatch, route):
    await _account("broken")

    def reject(*args, **kwargs):
        return _Response(401, b'{"error":{"message":"OAuth expired"}}')

    monkeypatch.setattr(AnthropicProxyService, "_open_upstream_response", reject)
    monkeypatch.setattr(AnthropicProxyService, "_open_count_tokens_response", reject)
    response = await async_client.post(f"/subscription/v1/{route}", json=_BODY)
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "subscription_login_required"
    assert "/login" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_all_message_rate_limits_remain_retryable_instead_of_becoming_login_required(
    async_client, monkeypatch,
):
    await _account("limited-first")
    await _account("limited-second")
    calls = []

    def reject(*args, **kwargs):
        calls.append(kwargs["headers"]["Authorization"])
        return _Response(
            429,
            b'{"type":"error","error":{"type":"rate_limit_error","message":"Opus is temporarily limited"}}',
            headers={"retry-after": "37"},
        )

    monkeypatch.setattr(AnthropicProxyService, "_open_upstream_response", reject)
    response = await async_client.post("/subscription/v1/messages", json=_BODY)

    assert response.status_code == 429
    assert response.json()["error"] == {
        "type": "rate_limit_error",
        "message": "Opus is temporarily limited",
    }
    assert 0 < int(response.headers["retry-after"]) <= 37
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_subscription_oauth_has_no_api_key_scope(async_client):
    response = await async_client.post("/api/oauth/start", json={"provider": "anthropic", "subscriptionOnly": True})
    assert response.status_code == 200
    link = urlparse(response.json()["authorizationUrl"])
    query = parse_qs(link.query)
    assert link.scheme == "https" and link.netloc == "claude.com" and link.path == "/cai/oauth/authorize"
    assert "org:create_api_key" not in query["scope"][0]
    assert "user:inference" in query["scope"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("billing_state", ["ready", "usage_unverified", "extra_usage_enabled"])
async def test_subscription_oauth_success_identifies_persisted_account(async_client, monkeypatch, billing_state):
    import app.modules.usage.updater as usage_module
    from app.core.clients.oauth import OAuthTokens
    from app.core.clients.usage import UsageFetchError
    from app.core.usage.models import CreditsPayload, RateLimitPayload, UsagePayload, UsageWindow
    from app.modules.oauth.service import OauthService

    async def exchange(self, provider_name, **kwargs):
        return OAuthTokens(
            access_token="sk-ant-oat01-new", refresh_token="sk-ant-ort01-new",
            account_id="new-account", email="new@example.com", plan_type="max", id_token=None,
        )

    monkeypatch.setattr(OauthService, "_exchange_authorization_code", exchange)
    async def fetch_usage(**kwargs):
        assert kwargs["access_token"] == "sk-ant-oat01-new"
        if billing_state == "usage_unverified":
            raise UsageFetchError(429, "Usage temporarily unavailable")
        return UsagePayload(
            rate_limit=RateLimitPayload(primary_window=UsageWindow(used_percent=0, limit_window_seconds=18000)),
            credits=CreditsPayload(has_credits=billing_state == "extra_usage_enabled", unlimited=False),
        )

    monkeypatch.setattr(usage_module, "fetch_anthropic_usage", fetch_usage)
    monkeypatch.setattr(AnthropicProxyService, "_open_upstream_response", lambda *args, **kwargs: _Response())
    start = (await async_client.post(
        "/api/oauth/start", json={"provider": "anthropic", "subscriptionOnly": True},
    )).json()
    state = parse_qs(urlparse(start["authorizationUrl"]).query)["state"][0]
    callback = await async_client.post(
        "/api/oauth/manual-callback", json={"flowId": start["flowId"], "callbackUrl": f"test-code#{state}"},
    )
    assert callback.json()["status"] == "success"
    status = (await async_client.get("/api/oauth/status", params={"flowId": start["flowId"]})).json()
    assert status["email"] == "new@example.com"
    assert status["accountId"] == callback.json()["accountId"]
    assert status["routingStatus"] == billing_state
    assert set(status) == {"status", "errorMessage", "accountId", "email", "routingStatus"}
    async with SessionLocal() as session:
        saved = await session.get(Account, status["accountId"])
        assert saved is not None and saved.email == "new@example.com"
    immediate_message = await async_client.post("/subscription/v1/messages", json=_BODY)
    assert immediate_message.status_code == (200 if billing_state == "ready" else 403)


@pytest.mark.asyncio
async def test_cancel_prevents_delayed_exchange_from_persisting(async_client, monkeypatch):
    import asyncio

    from app.core.clients.oauth import OAuthTokens
    from app.modules.oauth.service import OauthService

    exchanging = asyncio.Event()
    resume = asyncio.Event()

    async def exchange(self, provider_name, **kwargs):
        exchanging.set()
        await resume.wait()
        return OAuthTokens(
            access_token="sk-ant-oat01-cancel", refresh_token="sk-ant-ort01-cancel",
            account_id="cancelled-account", email="cancel@example.com", plan_type="max", id_token=None,
        )

    monkeypatch.setattr(OauthService, "_exchange_authorization_code", exchange)
    start = (await async_client.post(
        "/api/oauth/start", json={"provider": "anthropic", "subscriptionOnly": True},
    )).json()
    state = parse_qs(urlparse(start["authorizationUrl"]).query)["state"][0]
    pending = asyncio.create_task(async_client.post(
        "/api/oauth/manual-callback", json={"flowId": start["flowId"], "callbackUrl": f"test-code#{state}"},
    ))
    try:
        await asyncio.wait_for(exchanging.wait(), 2)
        cancelled = await async_client.post("/api/oauth/cancel", json={"flowId": start["flowId"]})
        assert cancelled.json()["status"] == "cancelled"
    finally:
        resume.set()
        result = await pending
    assert result.json()["status"] == "error"
    status = (await async_client.get("/api/oauth/status", params={"flowId": start["flowId"]})).json()
    assert status["status"] == "cancelled"
    late = await async_client.post(
        "/api/oauth/manual-callback", json={"flowId": start["flowId"], "callbackUrl": f"test-code#{state}"},
    )
    assert late.json()["status"] == "error"
    async with SessionLocal() as session:
        assert list((await session.scalars(select(Account))).all()) == []


@pytest.mark.asyncio
async def test_explicit_login_usage_cancellation_owns_its_work(async_client, monkeypatch):
    import asyncio

    import app.modules.usage.updater as usage_module
    from app.modules.oauth.subscription_usage import refresh_subscription_login_usage

    await _account("cancel-usage")
    fetching = asyncio.Event()
    cancelled = asyncio.Event()

    async def fetch_usage(**kwargs):
        fetching.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(usage_module, "fetch_anthropic_usage", fetch_usage)
    task = asyncio.create_task(refresh_subscription_login_usage("cancel-usage"))
    await asyncio.wait_for(fetching.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
