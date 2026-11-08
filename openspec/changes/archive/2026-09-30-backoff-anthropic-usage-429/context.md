# Incident and verification

On September 30, Sage Control's strict subscription route rejected all Claude
accounts after their genuine billing snapshots aged beyond 15 minutes. The
usage poller retried each usage 429 three times and repeated every scheduler
pass. A direct official usage GET confirmed `Retry-After: 0`; this must use the
fallback cooldown rather than authorize immediate polling.

All seven active accounts passed the existing minimal authentication probe.
Two accounts refreshed their tokens through normal `ensure_fresh(force=False)`
and obtained fresh usage snapshots. No billing settings, evidence timestamps,
or eligibility rules were overridden. Other older credentials remained
throttled; recovery of those credentials was not claimed.

Verification completed before deployment:

- 224 focused unit/integration tests passed (seven websocket tests deselected).
- Ruff passed for `app` and `clients`; all 37 OpenSpec specs passed strict validation.
- A real localhost HTTP fixture returned 429 with `Retry-After: 0`; two calls
  through the actual usage client caused one upstream request and a 300-second cooldown.
- An isolated Sage Control WebKit dev app clicked the real composer Send button.
  Its native Claude SDK used Opus 5.5 and the live strict subscription router;
  the assistant rendered `CHAT_SUBSCRIPTION_OK`. Login start/cancel RPC also passed.
- The temporary dev environment and session were removed. Production app,
  host daemon, account router, and user sessions were not restarted.

The code changes require an account-router restart to become active. The live
chat recovery above occurred through legitimate account refreshes, before that
restart. This distinction matters when assessing deployment status.
