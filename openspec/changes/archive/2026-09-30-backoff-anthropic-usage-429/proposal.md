# Change: Back off Anthropic usage throttling

## Why

Anthropic's OAuth usage endpoint throttles much more aggressively than the
inference endpoint. The shared 60-second usage cadence and generic retry policy
currently turn one usage `429` into three immediate requests and then repeat the
same work on every scheduler pass. Eventually every billing snapshot becomes
older than the subscription route's 15-minute safety bound and safe Claude
inference fails closed.

## What Changes

Add an Anthropic-specific five-minute freshness cadence and a credential-scoped
usage cooldown. A `429` is not retried immediately; its valid `Retry-After`
future value is honored, with a conservative five-minute fallback for missing,
invalid, zero, or elapsed hints. Rotating the OAuth
access token produces a new credential identity and may attempt usage
immediately. Successful usage fetches clear the credential cooldown.

The subscription route's 15-minute evidence requirement and extra-usage checks
remain unchanged. Failed usage requests never refresh persisted evidence.
