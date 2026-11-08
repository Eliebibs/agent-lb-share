# Subscription-only Claude routing

## Why
Sage Control needs a local route that bypasses stale client login state while preserving an absolute subscription-only billing policy. The general messages route can select other providers or opt into paid extra usage.

## What Changes
- Add an explicit Claude subscription route and policy discovery endpoint.
- Fail closed for non-OAuth credentials, non-Claude models, enabled/unknown/stale extra-usage state, and exhausted subscription capacity.
- Retry authentication failures before any upstream response is emitted, including token counting.
- Return the authenticated account identity on successful OAuth flows.

## Impact
Existing general proxy routes retain their behavior. No production restarts or account billing changes are part of validation.
