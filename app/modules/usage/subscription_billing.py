"""Independent billing evidence, never a primary/secondary quota measurement.

UsageHistory's numeric column is required; the dedicated window uses a zero
sentinel that MUST NOT participate in quota aggregates or quota selection.
"""

from __future__ import annotations

from collections.abc import Collection

from app.db.models import UsageHistory
from app.modules.usage.repository import UsageRepository

SUBSCRIPTION_BILLING_WINDOW = "subscription_billing"


async def latest_subscription_billing(
    repository: UsageRepository, account_ids: Collection[str],
) -> dict[str, UsageHistory]:
    """Prefer explicit observations, including unknown flags, over legacy rows."""
    legacy = await repository.latest_by_account(window="primary", account_ids=account_ids)
    observations = await repository.latest_by_account(window=SUBSCRIPTION_BILLING_WINDOW, account_ids=account_ids)
    return {**legacy, **observations}
