## 1. Specification

- [x] Define Anthropic usage cadence and `429` backoff behavior.

## 2. Implementation

- [x] Give Anthropic usage refresh a 300-second default freshness interval.
- [x] Disable immediate `429` retries for Anthropic usage while preserving other retries.
- [x] Add credential-scoped `Retry-After` cooldown with a 300-second fallback and success clearing.

## 3. Verification

- [x] Add client tests for seconds/date/fallback cooldowns, token rotation, success clearing, and retry behavior.
- [x] Add updater coverage for provider-specific freshness and unchanged account state on `429`.
- [x] Run focused tests, Ruff, and strict OpenSpec validation.
