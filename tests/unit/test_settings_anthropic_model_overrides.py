"""Tests for `Settings.resolve_anthropic_model_override`.

The helper backs operator-configured model pinning
(`AGENT_LB_ANTHROPIC_MODEL_OVERRIDES`): inbound Anthropic model ids are
rewritten before routing/quota/sticky derivation in `/v1/messages` and
`count_tokens`. A wrong resolution silently serves the wrong model for
every request, so the matching rules get focused coverage:

  1. Empty map disables the feature (returns None for everything).
  2. Exact keys map verbatim.
  3. Keys ending in "*" match by prefix, covering dated snapshots and
     future version ids without a config change.
  4. Exact keys win over overlapping wildcard keys.
  5. Models matching no key pass through (None) — deliberate per-client
     choices are untouched.
"""

from __future__ import annotations

import pytest

from app.core.config.settings import Settings

pytestmark = pytest.mark.unit


def _settings(overrides: dict[str, str]) -> Settings:
    return Settings(anthropic_model_overrides=overrides)


def test_empty_map_disables_overrides() -> None:
    assert _settings({}).resolve_anthropic_model_override("claude-opus-4-8") is None


def test_exact_match_maps_model() -> None:
    settings = _settings({"claude-opus-4-8": "claude-fable-5"})
    assert settings.resolve_anthropic_model_override("claude-opus-4-8") == "claude-fable-5"


def test_wildcard_prefix_matches_variants() -> None:
    settings = _settings({"claude-opus-*": "claude-fable-5"})
    for model in (
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-4-8-20260115",
        "claude-opus-5",
    ):
        assert settings.resolve_anthropic_model_override(model) == "claude-fable-5"


def test_exact_key_wins_over_wildcard() -> None:
    settings = _settings(
        {
            "claude-opus-*": "claude-fable-5",
            "claude-opus-4-5": "claude-sonnet-5",
        }
    )
    assert settings.resolve_anthropic_model_override("claude-opus-4-5") == "claude-sonnet-5"
    assert settings.resolve_anthropic_model_override("claude-opus-4-8") == "claude-fable-5"


def test_unmatched_models_pass_through() -> None:
    settings = _settings({"claude-opus-*": "claude-fable-5"})
    for model in ("claude-fable-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"):
        assert settings.resolve_anthropic_model_override(model) is None
