## ADDED Requirements

### Requirement: Strict subscription failures preserve upstream classification

The strict Claude subscription route MUST classify terminal candidate
exhaustion from the last real upstream result. When every attempted candidate
is rejected for quota, it MUST return a retryable rate-limit response and MUST
NOT describe the failure as an authentication problem. When every attempted
candidate is rejected for authentication, it MUST retain the guarded
`subscription_login_required` response.

#### Scenario: Every strict candidate is rate limited

- **GIVEN** multiple safe Claude subscription candidates
- **WHEN** every attempted candidate returns upstream `429`
- **THEN** the strict Messages route returns `429` with rate-limit semantics
- **AND** it preserves bounded retry metadata when available
- **AND** it does not tell the client to log in

#### Scenario: Every strict candidate rejects authentication

- **GIVEN** one or more safe Claude subscription candidates
- **WHEN** every attempted candidate returns upstream `401` or `403`
- **THEN** the strict route returns `403 subscription_login_required`
- **AND** it tells the operator to reconnect through Sage Control
