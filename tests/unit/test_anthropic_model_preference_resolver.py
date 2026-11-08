"""Unit matrix for quota-aware dynamic model preference.

The proxy re-resolves an inbound Anthropic model per request against a
configured, most-preferred-first chain (`anthropic_model_preference`):

  * highest-preference member with an eligible pool wins,
  * terminal (last) member is the fallback when nothing preferred is eligible.

For Elie's 2-element chain ``[fable, opus]`` this is exactly one eligibility
check (Fable):

  * Fable eligible  -> Fable  (keep Fable / upgrade Opus->Fable),
  * Fable exhausted -> Opus   (downgrade Fable->Opus / keep Opus).

Symmetric and automatic; re-evaluated every request. Empty chain (default) is a
byte-for-byte passthrough — the disabled-by-default guarantee.

Eligibility is driven by ``quota_key + model``: the Fable-scoped-weekly gate
lives inside ``_provider_quota_eligibility`` under its ``fable_request`` branch,
so the resolver MUST pass ``model=candidate``. These tests mock that method and
assert both the returned model and the exact ``(quota_key, model)`` it is probed
with.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.anthropic.models import AnthropicMessageRequest
from app.modules.proxy import anthropic_service as anthropic_service_module
from app.modules.proxy.anthropic_service import (
    AnthropicProxyService,
    _anthropic_messages_quota_key_for,
    _AnthropicQuotaEligibility,
)

pytestmark = pytest.mark.unit

_FABLE = "claude-fable-5"
_OPUS = "claude-opus-4-8"
_FABLE_OPUS_CHAIN = [_FABLE, _OPUS]


def _set_preference(monkeypatch: pytest.MonkeyPatch, chain: list[str]) -> None:
    monkeypatch.setattr(
        anthropic_service_module,
        "get_settings",
        lambda: SimpleNamespace(anthropic_model_preference=chain),
    )


def _service_with_eligible(eligible_models: set[str]) -> tuple[AnthropicProxyService, list[dict[str, Any]]]:
    """Build a service whose ``_provider_quota_eligibility`` reports the given
    models as pool-eligible and records every probe."""

    calls: list[dict[str, Any]] = []
    service = AnthropicProxyService(lambda: None)  # repo factory never touched — eligibility is mocked

    async def fake_eligibility(
        provider_name: str,
        quota_key: str,
        *,
        model: str | None = None,
    ) -> _AnthropicQuotaEligibility:
        calls.append({"provider_name": provider_name, "quota_key": quota_key, "model": model})
        account_ids = ["acc-1"] if model in eligible_models else []
        return _AnthropicQuotaEligibility(account_ids=account_ids)

    service._provider_quota_eligibility = fake_eligibility  # type: ignore[method-assign]
    return service, calls


# --- 1. disabled-by-default guarantee -------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [_FABLE, _OPUS, "claude-haiku-4-5", "glm-4.6"])
async def test_empty_chain_is_passthrough(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    _set_preference(monkeypatch, [])
    service, calls = _service_with_eligible({_FABLE})

    result = await service._resolve_preferred_model(
        model,
        provider_name="anthropic",
        thinking=False,
        fast_mode=False,
    )

    assert result == model
    assert calls == []  # no eligibility probe when disabled


# --- 2-5. the 2x2 flip matrix (inbound x fable-availability) ---------------


@pytest.mark.asyncio
async def test_fable_eligible_inbound_fable_keeps_fable(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_preference(monkeypatch, _FABLE_OPUS_CHAIN)
    service, _ = _service_with_eligible({_FABLE})

    result = await service._resolve_preferred_model(
        _FABLE, provider_name="anthropic", thinking=False, fast_mode=False
    )

    assert result == _FABLE


@pytest.mark.asyncio
async def test_fable_exhausted_inbound_fable_downgrades_to_opus(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_preference(monkeypatch, _FABLE_OPUS_CHAIN)
    service, _ = _service_with_eligible(set())  # nothing eligible -> fable check fails

    result = await service._resolve_preferred_model(
        _FABLE, provider_name="anthropic", thinking=False, fast_mode=False
    )

    assert result == _OPUS


@pytest.mark.asyncio
async def test_fable_eligible_inbound_opus_upgrades_to_fable(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_preference(monkeypatch, _FABLE_OPUS_CHAIN)
    service, _ = _service_with_eligible({_FABLE})

    result = await service._resolve_preferred_model(
        _OPUS, provider_name="anthropic", thinking=False, fast_mode=False
    )

    assert result == _FABLE


@pytest.mark.asyncio
async def test_fable_exhausted_inbound_opus_keeps_opus(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_preference(monkeypatch, _FABLE_OPUS_CHAIN)
    service, _ = _service_with_eligible(set())

    result = await service._resolve_preferred_model(
        _OPUS, provider_name="anthropic", thinking=False, fast_mode=False
    )

    assert result == _OPUS


# --- 6. terminal fallback logs a warning ----------------------------------


@pytest.mark.asyncio
async def test_terminal_fallback_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _set_preference(monkeypatch, _FABLE_OPUS_CHAIN)
    service, _ = _service_with_eligible(set())  # neither eligible

    with caplog.at_level("WARNING", logger="app.modules.proxy.anthropic_service"):
        result = await service._resolve_preferred_model(
            _FABLE, provider_name="anthropic", thinking=False, fast_mode=False
        )

    assert result == _OPUS
    assert any(record.levelname == "WARNING" for record in caplog.records)


# --- 7. model not in chain -> passthrough, no probe -----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["claude-haiku-4-5", "claude-sonnet-5"])
async def test_model_not_in_chain_passthrough_no_probe(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    _set_preference(monkeypatch, _FABLE_OPUS_CHAIN)
    service, calls = _service_with_eligible({_FABLE})

    result = await service._resolve_preferred_model(
        model, provider_name="anthropic", thinking=False, fast_mode=False
    )

    assert result == model
    assert calls == []


# --- 8. non-anthropic provider -> passthrough -----------------------------


@pytest.mark.asyncio
async def test_non_anthropic_provider_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even a model that *is* in the chain must pass through for a non-anthropic
    # provider — the fable gate only applies to the anthropic pool.
    _set_preference(monkeypatch, _FABLE_OPUS_CHAIN)
    service, calls = _service_with_eligible({_FABLE})

    result = await service._resolve_preferred_model(
        _FABLE, provider_name="glm", thinking=False, fast_mode=False
    )

    assert result == _FABLE
    assert calls == []


# --- 9. thinking request -> candidate key is anthropic_top_thinking -------


@pytest.mark.asyncio
async def test_thinking_request_probes_thinking_quota_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_preference(monkeypatch, _FABLE_OPUS_CHAIN)
    service, calls = _service_with_eligible({_FABLE})

    await service._resolve_preferred_model(
        _OPUS, provider_name="anthropic", thinking=True, fast_mode=False
    )

    assert calls == [{"provider_name": "anthropic", "quota_key": "anthropic_top_thinking", "model": _FABLE}]


# --- 10. fast-mode request -> candidate key is anthropic_fast --------------


@pytest.mark.asyncio
async def test_fast_mode_request_probes_fast_quota_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_preference(monkeypatch, _FABLE_OPUS_CHAIN)
    service, calls = _service_with_eligible({_FABLE})

    await service._resolve_preferred_model(
        _OPUS, provider_name="anthropic", thinking=False, fast_mode=True
    )

    assert calls == [{"provider_name": "anthropic", "quota_key": "anthropic_fast", "model": _FABLE}]


# --- 11. eligibility probed with model=candidate (quota_key+model nuance) --


@pytest.mark.asyncio
async def test_eligibility_probed_with_candidate_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_preference(monkeypatch, _FABLE_OPUS_CHAIN)
    service, calls = _service_with_eligible({_FABLE})

    await service._resolve_preferred_model(
        _OPUS, provider_name="anthropic", thinking=False, fast_mode=False
    )

    # Exactly one probe, for the non-terminal candidate, with model=candidate
    # and the plain "anthropic_top" base key.
    assert calls == [{"provider_name": "anthropic", "quota_key": "anthropic_top", "model": _FABLE}]


# --- 12. resolver never mutates the inbound request object -----------------


@pytest.mark.asyncio
async def test_resolver_does_not_mutate_inbound_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_preference(monkeypatch, _FABLE_OPUS_CHAIN)
    service, _ = _service_with_eligible(set())  # force downgrade path

    payload = AnthropicMessageRequest(
        model=_FABLE,
        max_tokens=16,
        messages=[{"role": "user", "content": "hi"}],
        thinking={"type": "enabled"},
    )
    before = payload.model_dump()

    result = await service._resolve_preferred_model(
        payload.model,
        provider_name="anthropic",
        thinking=bool(payload.thinking),
        fast_mode=False,
    )

    # Resolver operates on extracted primitives — it returns the new model id
    # but never touches the caller's request object.
    assert result == _OPUS
    assert payload.model_dump() == before


# --- candidate quota-key helper (pure) ------------------------------------


@pytest.mark.parametrize(
    ("model", "thinking", "fast_mode", "expected"),
    [
        (_FABLE, False, False, "anthropic_top"),
        (_OPUS, False, False, "anthropic_top"),
        (_FABLE, True, False, "anthropic_top_thinking"),
        (_OPUS, True, False, "anthropic_top_thinking"),
        (_FABLE, False, True, "anthropic_fast"),
        (_OPUS, True, True, "anthropic_fast"),  # fast-mode wins over thinking
        ("claude-haiku-4-5", False, False, "anthropic_standard"),
    ],
)
def test_candidate_quota_key(model: str, thinking: bool, fast_mode: bool, expected: str) -> None:
    assert _anthropic_messages_quota_key_for(model, thinking=thinking, fast_mode=fast_mode) == expected
