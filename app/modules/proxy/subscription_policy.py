"""Fail-closed eligibility for clients that must never use metered credentials."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

from app.core.crypto import TokenEncryptor
from app.core.utils.time import naive_utc_to_epoch
from app.db.models import Account, UsageHistory

# Provider usage GETs can return 429 for several minutes even while inference
# is healthy. Keep a bounded previously-verified disabled billing state.
_USAGE_MAX_AGE_SECONDS = 15 * 60


def billing_snapshot_ready(snapshot: UsageHistory | None) -> bool:
    return (
        snapshot is not None
        and snapshot.credits_has is False
        and snapshot.credits_unlimited is False
        and 0 <= time.time() - naive_utc_to_epoch(snapshot.recorded_at) <= _USAGE_MAX_AGE_SECONDS
    )


def validate_subscription_model(model: str) -> None:
    # Import lazily to keep the policy independent of service construction.
    from app.modules.proxy.anthropic_service import AnthropicProxyError

    if not model.startswith("claude-"):
        raise AnthropicProxyError(
            400, "This route accepts Claude subscription models only.", code="subscription_model_required",
        )


def validate_oauth_credentials(account: Account, encryptor: TokenEncryptor) -> None:
    from app.modules.proxy.anthropic_service import AnthropicProxyError

    if (
        account.provider != "anthropic"
        or not encryptor.decrypt(account.access_token_encrypted).startswith("sk-ant-oat01-")
        or not encryptor.decrypt(account.refresh_token_encrypted).startswith("sk-ant-ort01-")
    ):
        raise AnthropicProxyError(
            401, "Claude subscription OAuth login is required. API keys are never accepted.",
            code="subscription_oauth_required",
        )


def subscription_eligible_ids(
    accounts: Sequence[Account],
    account_ids: list[str],
    usage: Mapping[str, UsageHistory],
    encryptor: TokenEncryptor,
    *,
    require_disabled_extra_usage: bool,
) -> list[str]:
    from app.modules.proxy.anthropic_service import AnthropicProxyError

    allowed = set(account_ids)
    result: list[str] = []
    for account in accounts:
        if account.id not in allowed:
            continue
        try:
            validate_oauth_credentials(account, encryptor)
        except AnthropicProxyError:
            continue
        if require_disabled_extra_usage:
            snapshot = usage.get(account.id)
            if not billing_snapshot_ready(snapshot):
                continue
        result.append(account.id)
    return result
