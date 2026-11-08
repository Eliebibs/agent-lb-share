# Validation and operating contract

Sage Control supplies a local sentinel bearer to `/subscription`; the sentinel is
never an upstream credential. Existing proxy authentication remains in force,
and the route replaces credentials with selected Claude OAuth tokens.

Inference only selects accounts with OAuth token types and a primary usage
snapshot no older than 15 minutes explicitly reporting extra usage disabled.
The normal usage scheduler defaults to 60 seconds. Unknown billing state fails
closed. The per-request policy also disables the global opt-in paid fallback.
The initial 120-second window was rejected after real testing: Claude's usage
endpoint returned HTTP 429 while genuine disabled-extra-usage snapshots became
201–347 seconds old and inference remained usable. The fixed 15-minute bound
tolerates that provider throttle without fabricating fresh timestamps or silently
widening the policy further.
Token counting is free and does not require a recent billing snapshot, but it
still requires subscription OAuth credentials. Other provider models and aliases
are rejected before dispatch and again after operator model overrides.

This controls routing, not the provider billing dashboard. An external actor
changing extra-usage settings immediately after the last usage snapshot cannot
be ruled out atomically by the provider API. Keep extra usage disabled at Claude
to preserve the subscription-only policy. No account billing setting was changed.

Authentication rejection exhausted across eligible accounts returns HTTP 403 with
an actionable `/login` message. Native Claude retries 401 by trying local token
refresh and retries 503 as transient; 403 displays the corrective action promptly.

Validation on 2026-09-25 included route tests for forbidden models/credentials,
unknown billing state, stale usage, paid fallback enabled globally, upstream auth
failover, refresh-invalid failover, account identity and cancellation during OAuth
exchange. The standard Anthropic and OAuth suites were also exercised.

A minimal isolated HTTP server on port 2456 used a private copy of the account DB,
disabled all background schedulers and refresh-token rotation, and fetched real
subscription usage before inference. The real native Claude SDK, with an empty
credential home and denied direct external network access, returned
`SUBSCRIPTION_OK` through this route using Haiku. Production app, daemon and agent-lb
were not restarted. Real subscription OAuth start/cancel also succeeded. Browser
consent completion still requires the account owner's consent and was not replayed.

Subscription login immediately requests safe usage through the existing updater,
bounded to five seconds without inference or refresh-token rotation. Successful callback/status responses
include `routingStatus: ready|usage_unverified|extra_usage_enabled`; transient usage
errors preserve token-login success and never falsely promise routing readiness.
