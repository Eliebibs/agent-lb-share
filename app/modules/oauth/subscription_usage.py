"""Read subscription billing eligibility immediately after an explicit login."""

from __future__ import annotations

from typing import Literal

from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.subscription_policy import billing_snapshot_ready
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.subscription_billing import latest_subscription_billing
from app.modules.usage.updater import UsageUpdater

SubscriptionRoutingStatus = Literal["ready", "usage_unverified", "extra_usage_enabled"]


async def refresh_subscription_login_usage(account_id: str) -> SubscriptionRoutingStatus:
    async with get_background_session() as session:
        account = await AccountsRepository(session).get_by_id(account_id)
        if account is None:
            return "usage_unverified"
        usage = UsageRepository(session)
        # Newly issued access token only: no refresh-token rotation is needed
        # for this safe usage GET and no inference probe is sent.
        updater = UsageUpdater(usage, additional_usage_repo=AdditionalUsageRepository(session))
        refreshed = await updater.force_refresh(account, explicit_request=True)
        snapshot = (await latest_subscription_billing(usage, [account_id])).get(account_id)
        if not refreshed or snapshot is None or snapshot.credits_has is None:
            return "usage_unverified"
        if snapshot.credits_has is not False or snapshot.credits_unlimited is not False:
            return "extra_usage_enabled"
        if not billing_snapshot_ready(snapshot):
            return "usage_unverified"
    return "ready"
