## 1. Implementation

- [x] 1.1 Add the anthropic-messages-compat delta for operator model overrides.
- [x] 1.2 Add `anthropic_model_overrides` setting + `resolve_anthropic_model_override` (exact-first, `*` prefix wildcard).
- [x] 1.3 Rewrite the model at both proxy entry points (`stream_messages`, `count_tokens`) before routing/logging/dispatch.

## 2. Validation and rollout

- [x] 2.1 Unit tests for the resolver (empty map, exact, wildcard, precedence, pass-through); ruff; proxy unit suite green.
- [x] 2.2 Deploy on the Mac mini launchd plist, restart the live service, and prove an inbound `claude-opus-4-8` request is served and logged as `claude-fable-5` end-to-end through the real client path.
