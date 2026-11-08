from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin

import aiohttp
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.clients.http import lease_retry_client
from app.core.clients.usage import (
    RETRYABLE_STATUS,
    UsageFetchError,
    _extract_error_code,
    _extract_error_message,
    _retry_options,
)
from app.core.config.settings import get_settings
from app.core.usage.models import CreditsPayload, RateLimitPayload, UsagePayload, UsageWindow
from app.core.utils.request_id import get_request_id

logger = logging.getLogger(__name__)

_FIVE_HOUR_SECONDS = 5 * 60 * 60
_SEVEN_DAY_SECONDS = 7 * 24 * 60 * 60
_RATE_LIMIT_FALLBACK_SECONDS = 300.0
_usage_rate_limit_cooldowns: dict[str, float] = {}


class AnthropicUsageWindow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    utilization: float | None = None
    resets_at: str | None = None


class AnthropicExtraUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    is_enabled: bool | None = None
    # ``monthly_limit`` and ``used_credits`` arrive in currency minor units
    # (e.g. cents for USD); ``decimal_places`` says how to scale them.
    monthly_limit: float | None = None
    used_credits: float | None = None
    utilization: float | None = None
    currency: str | None = None
    decimal_places: int | None = None


class AnthropicLimitScopeModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    display_name: str | None = None


class AnthropicLimitScope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: AnthropicLimitScopeModel | None = None
    surface: str | None = None


class AnthropicLimitEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str | None = None
    group: str | None = None
    percent: float | None = None
    severity: str | None = None
    resets_at: str | None = None
    scope: AnthropicLimitScope | None = None
    is_active: bool | None = None


class AnthropicOAuthUsagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    five_hour: AnthropicUsageWindow | None = None
    seven_day: AnthropicUsageWindow | None = None
    extra_usage: AnthropicExtraUsage | None = None
    limits: list[AnthropicLimitEntry] | None = None


async def fetch_anthropic_usage(
    *,
    access_token: str,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    client: aiohttp.ClientSession | None = None,
) -> UsagePayload:
    settings = get_settings()
    usage_base = base_url or settings.anthropic_upstream_base_url
    url = urljoin(usage_base.rstrip("/") + "/", "api/oauth/usage")
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or settings.usage_fetch_timeout_seconds)
    retries = max_retries if max_retries is not None else settings.usage_fetch_max_retries
    headers = _anthropic_usage_headers(access_token)
    credential_key = _credential_key(access_token)
    _raise_if_usage_rate_limited(credential_key)
    retry_options = _retry_options(retries + 1, statuses=RETRYABLE_STATUS - {429})

    try:
        if client is not None:
            async with client.get(url, headers=headers, timeout=timeout) as resp:
                return await _usage_payload_or_raise(resp, credential_key=credential_key)
        async with lease_retry_client() as retry_client:
            async with retry_client.request(
                "GET",
                url,
                headers=headers,
                timeout=timeout,
                retry_options=retry_options,
            ) as resp:
                return await _usage_payload_or_raise(resp, credential_key=credential_key)
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("Anthropic usage fetch error request_id=%s error=%s", get_request_id(), exc)
        raise UsageFetchError(0, f"Anthropic usage fetch failed: {exc}") from exc


def _usage_payload_from_anthropic(payload: AnthropicOAuthUsagePayload) -> UsagePayload:
    return UsagePayload(
        plan_type="claude",
        rate_limit=RateLimitPayload(
            primary_window=_usage_window(payload.five_hour, limit_window_seconds=_FIVE_HOUR_SECONDS),
            secondary_window=_usage_window(payload.seven_day, limit_window_seconds=_SEVEN_DAY_SECONDS),
        ),
        credits=_credits_from_extra_usage(payload.extra_usage),
        fable_scoped_weekly=_fable_scoped_weekly_window(_fable_scoped_weekly_entry(payload.limits)),
    )


def _fable_scoped_weekly_entry(limits: list[AnthropicLimitEntry] | None) -> AnthropicLimitEntry | None:
    if not limits:
        return None
    for entry in limits:
        if entry.kind != "weekly_scoped":
            continue
        display_name = entry.scope.model.display_name if entry.scope and entry.scope.model else None
        if display_name is not None and display_name.strip().lower() == "fable":
            return entry
    return None


def _fable_scoped_weekly_window(entry: AnthropicLimitEntry | None) -> UsageWindow | None:
    if entry is None or entry.percent is None:
        return None
    return UsageWindow(
        used_percent=float(entry.percent),
        reset_at=_parse_rfc3339_epoch(entry.resets_at),
        limit_window_seconds=_SEVEN_DAY_SECONDS,
    )


def _credits_from_extra_usage(extra: AnthropicExtraUsage | None) -> CreditsPayload | None:
    if extra is None or extra.is_enabled is None:
        return None
    if extra.is_enabled is not True:
        return CreditsPayload(has_credits=False, unlimited=False, balance=None)
    return CreditsPayload(
        has_credits=True,
        unlimited=False,
        balance=_extra_usage_balance(extra),
    )


def _extra_usage_balance(extra: AnthropicExtraUsage) -> str | None:
    if extra.monthly_limit is None or extra.decimal_places is None or extra.decimal_places < 0:
        return None
    remaining_minor = float(extra.monthly_limit) - float(extra.used_credits or 0.0)
    return str(max(remaining_minor, 0.0) / 10**extra.decimal_places)


def _usage_window(
    window: AnthropicUsageWindow | None,
    *,
    limit_window_seconds: int,
) -> UsageWindow | None:
    if window is None or window.utilization is None:
        return None
    return UsageWindow(
        used_percent=float(window.utilization),
        reset_at=_parse_rfc3339_epoch(window.resets_at),
        limit_window_seconds=limit_window_seconds,
    )


def _parse_rfc3339_epoch(value: str | None) -> int | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        return None


def _anthropic_usage_headers(access_token: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "claude-cli/2.1.170",
    }
    request_id = get_request_id()
    if request_id:
        headers["x-request-id"] = request_id
    return headers


async def _safe_json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        return {"error": {"message": text.strip()}}
    return data if isinstance(data, dict) else {"error": {"message": str(data)}}


async def _usage_payload_or_raise(
    resp: aiohttp.ClientResponse,
    *,
    credential_key: str,
) -> UsagePayload:
    data = await _safe_json(resp)
    if resp.status >= 400:
        if resp.status == 429:
            _mark_usage_rate_limited(
                credential_key,
                retry_after=resp.headers.get("Retry-After"),
            )
        code = _extract_error_code(data)
        message = _extract_error_message(data) or f"Anthropic usage fetch failed ({resp.status})"
        logger.warning(
            "Anthropic usage fetch failed request_id=%s status=%s code=%s message=%s",
            get_request_id(),
            resp.status,
            code,
            message,
        )
        raise UsageFetchError(resp.status, message, code=code)
    try:
        payload = AnthropicOAuthUsagePayload.model_validate(data)
    except ValidationError as exc:
        logger.warning("Anthropic usage fetch invalid payload request_id=%s", get_request_id())
        raise UsageFetchError(502, "Invalid Anthropic usage payload") from exc
    _usage_rate_limit_cooldowns.pop(credential_key, None)
    return _usage_payload_from_anthropic(payload)


def _credential_key(access_token: str) -> str:
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()


def _raise_if_usage_rate_limited(credential_key: str) -> None:
    _prune_usage_rate_limit_cooldowns()
    expires_at = _usage_rate_limit_cooldowns.get(credential_key)
    if expires_at is None:
        return
    remaining = expires_at - time.monotonic()
    if remaining <= 0:
        _usage_rate_limit_cooldowns.pop(credential_key, None)
        return
    raise UsageFetchError(429, f"Anthropic usage refresh cooling down for {remaining:.0f} seconds")


def _mark_usage_rate_limited(credential_key: str, *, retry_after: str | None) -> None:
    delay = _retry_after_seconds(retry_after)
    _usage_rate_limit_cooldowns[credential_key] = time.monotonic() + delay


def _retry_after_seconds(value: str | None, *, now: datetime | None = None) -> float:
    if value:
        normalized = value.strip()
        try:
            seconds = float(normalized)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(normalized)
            except (TypeError, ValueError, OverflowError):
                pass
            else:
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                current = now or datetime.now(timezone.utc)
                seconds = (retry_at - current).total_seconds()
                if seconds > 0:
                    return seconds
        else:
            if math.isfinite(seconds) and seconds > 0:
                return seconds
    return _RATE_LIMIT_FALLBACK_SECONDS


def _prune_usage_rate_limit_cooldowns() -> None:
    now = time.monotonic()
    expired = [key for key, expires_at in _usage_rate_limit_cooldowns.items() if expires_at <= now]
    for key in expired:
        _usage_rate_limit_cooldowns.pop(key, None)
