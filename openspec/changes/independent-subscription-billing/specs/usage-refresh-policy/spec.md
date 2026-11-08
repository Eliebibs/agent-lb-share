## ADDED Requirements

### Requirement: Subscription billing evidence is independent of quota windows
Every successful Anthropic usage response SHALL persist its billing flags independently of quota windows. Missing billing flags SHALL invalidate earlier disabled evidence. Subscription routing and login readiness SHALL require the latest evidence to explicitly disable Extra Usage and be no older than 900 seconds. Unknown primary usage SHALL remain unknown. Failed usage requests SHALL NOT fabricate evidence.

#### Scenario: Five-hour window absent
- **WHEN** a successful usage response has no five-hour window and explicitly disables Extra Usage
- **THEN** subscription routing can use its fresh billing evidence without creating primary quota

#### Scenario: New evidence is unsafe
- **WHEN** a later successful response has enabled or missing billing flags
- **THEN** earlier disabled evidence cannot authorize inference
