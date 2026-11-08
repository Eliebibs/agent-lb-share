## Why

The operator's Claude sessions kept landing on Opus (client defaults drifting back to `claude-opus-4-8`, plus mid-session model switches by the harness), despite Fable being the intended model everywhere. Client-side defaults proved unreliable across Happy, Claude Code, and subagent spawns — every client is a separate place for the default to regress. The LB proxies every Anthropic request, so it is the single choke point where the effective model can be enforced.

## What Changes

- Add `AGENT_LB_ANTHROPIC_MODEL_OVERRIDES` (JSON map, default empty) that force-maps inbound Anthropic model ids before routing.
- Keys ending in `*` match by prefix (exact keys win), so dated snapshots and future version ids (`claude-opus-4-8-YYYYMMDD`, `claude-opus-5`) are covered without config changes.
- The rewrite happens before provider/quota/sticky derivation and the upstream body dump in both `/v1/messages` and `/v1/messages/count_tokens`, so routing, logging, and dispatch all see the effective model.
- Models matching no key pass through untouched — deliberate per-client model choices (e.g. Sonnet explore seats, Haiku background calls) are unaffected.

## Capabilities

### Modified Capabilities

- `anthropic-messages-compat`: Add operator model-override requirement (inbound model pinning before routing).

## Impact

`app/core/config/settings.py` (setting + resolver), `app/modules/proxy/anthropic_service.py` (two rewrite sites). Off by default; no behavior change unless the env var is set. Deployed on the Mac mini with `{"claude-opus-4-8": "claude-fable-5", "claude-opus-4-7": "claude-fable-5"}` plus the `claude-opus-*` wildcard.
