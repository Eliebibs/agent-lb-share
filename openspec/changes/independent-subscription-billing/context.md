# Implementation and verification

The observed usage response had `five_hour: null`, weekly utilization 26,
Fable-scoped utilization 43 and `extra_usage.is_enabled: false`. Previous
persistence discarded billing evidence without a primary window, eventually
expiring all subscription eligibility despite fresh successful usage requests.

`subscription_billing` is a dedicated non-quota UsageHistory window. Its required
numeric field holds a zero sentinel, not a utilization estimate. Quota selectors
already explicitly select primary/secondary/monthly; unfiltered aggregate/trend
queries now exclude all non-quota windows. No primary usage is synthesized.
Unknown observations supersede older disabled observations. Existing primary
evidence remains a migration-free fallback only until a dedicated observation
exists. The same 900-second fail-closed policy governs routing and login readiness.

Freshness uses the independent observation for the existing 300-second Anthropic
refresh cadence, preventing a null primary from causing 60-second polling.

Validation: 205 tests passed, one PostgreSQL-only query-plan test skipped; Ruff
passed for app and clients; strict OpenSpec change validation passed. Tests include
actual ASGI subscription routes against an isolated database and mocked upstream,
null-primary/no-window payloads, enabled/missing billing flags replacing disabled,
stale evidence, failed refresh, aggregates/trends and refresh cadence.

After explicit operator approval for a brief LB reconnect, HTTP traffic was drained
and only agent-lb was restarted (new PID 43513). The production desktop app was not
restarted; all 19 original agent wrapper PIDs/start identities were preserved.
The scheduler recorded fresh disabled-billing evidence for all eight active Claude
accounts at 2026-10-31 19:04:56–57 UTC. Three accounts retained Fable capacity;
exhausted account/model quotas remained enforced. The original personal Sage was
then exercised through the real dev app route and replied exactly
`CLAUDE_ROUTING_OK`, returning idle without the prior 403.
