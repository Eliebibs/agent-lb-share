## ADDED Requirements

### Requirement: Fable scoped exhaustion is identified separately

When a Fable request has no eligible account solely because every routable
candidate has an active `anthropic_fable_scoped_weekly` exhaustion, the
pool-wide `429` MUST identify `anthropic_fable_scoped_weekly` as the blocking
quota and retain the earliest scoped reset. It MUST NOT identify the shared
`anthropic_top` or `anthropic_top_thinking` request family as exhausted.
Routing eligibility, cooldown persistence, and paid fallback behavior MUST
remain unchanged.

#### Scenario: General weekly headroom but Fable is exhausted

- **GIVEN** all routable accounts have general five-hour and weekly headroom
- **AND** all have active Fable scoped weekly exhaustion
- **WHEN** a Fable request is routed
- **THEN** the response is `429` naming `anthropic_fable_scoped_weekly`
- **AND** its retry deadline is the earliest Fable scoped reset

