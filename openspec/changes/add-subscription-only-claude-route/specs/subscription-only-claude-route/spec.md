## ADDED Requirements

### Requirement: Explicit subscription-only route
The service SHALL expose `/subscription/policy`, `/subscription/v1/messages`, and `/subscription/v1/messages/count_tokens`. The policy SHALL declare provider `anthropic`, authentication `subscription-oauth`, `apiKeyFallback: false`, and `extraUsage: false`. Unknown subscription paths SHALL NOT forward upstream. Existing proxy authentication SHALL remain enforced.

#### Scenario: Incompatible model or route
- **WHEN** a request uses a non-Claude model or unknown subscription path
- **THEN** the service rejects it without upstream inference

### Requirement: Fail closed on billing uncertainty
The subscription route SHALL select only Anthropic accounts with recognized OAuth access and refresh credentials. Inference SHALL require a usage snapshot at most 15 minutes old reporting extra usage disabled. Enabled, unknown or stale extra-usage state SHALL exclude the account. Global paid fallback settings SHALL NOT override this policy. Model overrides SHALL NOT change the provider.

#### Scenario: Paid fallback enabled globally
- **WHEN** all subscription capacity is exhausted and paid fallback is enabled globally
- **THEN** the subscription route fails or waits for subscription reset without paid inference

#### Scenario: Unknown or enabled extra usage
- **WHEN** an account has no fresh confirmation that extra usage is disabled
- **THEN** the subscription route does not dispatch inference to that account

### Requirement: Authentication failover preserves request ownership
Subscription messages and token counting SHALL retry another eligible account after invalid refresh credentials or an upstream authentication rejection before any successful response is emitted. Attempts SHALL be bounded and failed accounts SHALL be excluded from the current request. Token counting SHALL NOT write inference usage or reservations.

#### Scenario: First account requires login
- **WHEN** the first account cannot refresh and another eligible account exists
- **THEN** the request succeeds using the other subscription without requiring a local Claude login

### Requirement: OAuth success identifies the authenticated account
Successful subscription-only flow status and completion responses SHALL include the persisted account ID and email, without access tokens, refresh tokens or PKCE secrets.
The service SHALL immediately attempt a safe usage refresh, bounded to five seconds, and SHALL return `routingStatus` as `ready`, `usage_unverified`, or `extra_usage_enabled`. Usage failure SHALL NOT discard successful token login or claim routing is ready.

#### Scenario: Reconnect a subscription
- **WHEN** a browser flow persists subscription credentials successfully
- **THEN** its status identifies the same account whose credentials were persisted

### Requirement: Subscription login excludes API-key authorization
OAuth start with `subscriptionOnly: true` SHALL use the Claude subscription consent page and SHALL omit `org:create_api_key`. It SHALL reject other providers. Operator authorization URL and scope overrides SHALL NOT widen this policy.

#### Scenario: Start chat login
- **WHEN** Sage Control starts subscription-only OAuth
- **THEN** the returned URL targets `https://claude.com/cai/oauth/authorize` and requests subscription inference permissions without API-key creation

### Requirement: Cancel invalidates pending authorization
The service SHALL expose `/api/oauth/cancel` with `flowId`. Cancellation SHALL invalidate PKCE and callback state, and SHALL prevent an in-flight exchange from persisting after cancellation wins. Completed successful flows SHALL retain their successful identity.

#### Scenario: Cancel during exchange
- **WHEN** the user cancels while the provider exchange is pending
- **THEN** the exchange result is discarded and no account credentials are stored
