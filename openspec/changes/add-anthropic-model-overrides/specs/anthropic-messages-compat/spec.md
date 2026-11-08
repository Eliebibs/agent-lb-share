## ADDED Requirements

### Requirement: Operator model overrides pin the effective Anthropic model

When `AGENT_LB_ANTHROPIC_MODEL_OVERRIDES` is configured, the service MUST rewrite an inbound Anthropic model id that matches an override entry to the mapped target before provider selection, quota derivation, sticky affinity, request logging, and upstream dispatch, for both `/v1/messages` and `/v1/messages/count_tokens`. Keys ending in `*` MUST match by prefix; an exact key MUST win over any wildcard key. Model ids matching no entry MUST pass through unchanged. An empty map MUST disable the feature entirely.

#### Scenario: Opus request is served as Fable

- **WHEN** the override map contains `{"claude-opus-*": "claude-fable-5"}` and a client sends `/v1/messages` with `"model": "claude-opus-4-8"`
- **THEN** the request routes, logs, and dispatches upstream as `claude-fable-5`

#### Scenario: Dated snapshot ids are covered by the wildcard

- **WHEN** the override map contains `{"claude-opus-*": "claude-fable-5"}` and a client sends `"model": "claude-opus-4-8-20260115"`
- **THEN** the request is rewritten to `claude-fable-5`

#### Scenario: Exact key beats wildcard

- **WHEN** the override map contains both `"claude-opus-*"` and an exact `"claude-opus-4-5"` entry
- **THEN** a `claude-opus-4-5` request maps to the exact entry's target

#### Scenario: Unmatched models pass through

- **WHEN** the override map contains only opus keys and a client sends `"model": "claude-sonnet-5"`
- **THEN** the request proceeds with `claude-sonnet-5` unchanged

#### Scenario: count_tokens sees the same rewrite

- **WHEN** the override map contains `{"claude-opus-*": "claude-fable-5"}` and a client calls `/v1/messages/count_tokens` with an opus model
- **THEN** counting is performed against `claude-fable-5`
