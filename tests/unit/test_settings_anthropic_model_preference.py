"""Tests for the `anthropic_model_preference` setting.

Backs quota-aware dynamic model substitution
(`AGENT_LB_ANTHROPIC_MODEL_PREFERENCE`): an ordered, most-preferred-first chain
of Anthropic model ids. When an inbound model is a member of the chain, the
proxy re-resolves it per request to the highest-preference member with an
eligible account pool-wide, falling back to the terminal member otherwise.

Parsing mirrors the sibling `anthropic_model_overrides` setting exactly: a plain
complex field that pydantic-settings JSON-decodes from the env var, empty by
default (feature disabled), fail-fast on malformed input.
"""

from __future__ import annotations

import pytest

from app.core.config.settings import Settings

pytestmark = pytest.mark.unit


def test_default_preference_is_empty() -> None:
    # Empty chain (default) disables the feature — zero behavior change.
    assert Settings().anthropic_model_preference == []


def test_preference_accepts_direct_list() -> None:
    settings = Settings(anthropic_model_preference=["claude-fable-5", "claude-opus-4-8"])
    assert settings.anthropic_model_preference == ["claude-fable-5", "claude-opus-4-8"]


def test_preference_parses_json_list_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AGENT_LB_ANTHROPIC_MODEL_PREFERENCE",
        '["claude-fable-5", "claude-opus-4-8"]',
    )
    assert Settings().anthropic_model_preference == ["claude-fable-5", "claude-opus-4-8"]


def test_preference_preserves_order_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AGENT_LB_ANTHROPIC_MODEL_PREFERENCE",
        '["claude-opus-4-8", "claude-fable-5"]',
    )
    assert Settings().anthropic_model_preference == ["claude-opus-4-8", "claude-fable-5"]


def test_malformed_env_json_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    # Matches anthropic_model_overrides: malformed complex env input raises at
    # Settings construction rather than silently coercing.
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_MODEL_PREFERENCE", "not-json")
    with pytest.raises(Exception):
        Settings()
