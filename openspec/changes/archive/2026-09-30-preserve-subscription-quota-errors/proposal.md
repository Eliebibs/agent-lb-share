# Change: Preserve strict subscription quota errors

## Why

When every strict Claude subscription candidate returns an upstream `429`, the
retry loop excludes those accounts and the selector currently mistakes the
resulting empty candidate set for an authentication failure. Clients receive a
misleading `403 subscription_login_required` even though their OAuth sessions
are valid.

## What Changes

Preserve the last real upstream failure classification when candidate
exhaustion follows failover. Quota exhaustion remains a retryable `429` with
the provider message and bounded retry metadata. Actual authentication
exhaustion remains the deliberate `403 subscription_login_required` response
that prevents SDK-managed credential refresh.
