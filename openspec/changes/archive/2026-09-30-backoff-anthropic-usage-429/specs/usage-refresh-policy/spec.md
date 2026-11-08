## ADDED Requirements

### Requirement: Anthropic usage refresh respects provider throttling

Background usage refresh SHALL treat a successful Anthropic usage snapshot as
fresh for 300 seconds by default while retaining the normal 60-second default
for other providers. An Anthropic usage `429` MUST NOT be retried immediately.
The client MUST suppress later usage calls for the same OAuth access credential
until a valid future upstream `Retry-After` deadline, or for 300 seconds when the
header is absent, invalid, zero, or already elapsed. A rotated access credential
MUST NOT inherit the old credential's cooldown, and a successful fetch MUST
clear its cooldown.
Usage throttling MUST NOT change account status or create a fabricated usage
snapshot.

#### Scenario: Anthropic usage is throttled

- **WHEN** Anthropic usage returns `429` without a valid future `Retry-After`
- **THEN** the client sends no immediate retry
- **AND** calls using the same OAuth access credential are suppressed for 300 seconds
- **AND** the account status and persisted usage evidence remain unchanged

#### Scenario: Upstream supplies a retry deadline

- **WHEN** Anthropic usage returns `429` with a valid delta-seconds or HTTP-date `Retry-After`
- **THEN** the credential cooldown lasts until that deadline

#### Scenario: Credential rotates during cooldown

- **WHEN** an Anthropic access credential is rotated while the previous credential is cooling down
- **THEN** the new credential may attempt usage immediately

#### Scenario: Other retryable failures remain transient

- **WHEN** Anthropic usage encounters a retryable transport or server failure other than `429`
- **THEN** the existing bounded retry behavior remains available
