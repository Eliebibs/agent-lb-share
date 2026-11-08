# Independent subscription billing evidence

## Why
Anthropic can return a null five-hour window with valid weekly quota and billing flags. Coupling billing verification to primary quota incorrectly blocks subscription traffic.

## What Changes
Persist a non-quota billing snapshot for every successful Anthropic usage response. Routing and login readiness use the same fresh fail-closed policy. Missing or enabled flags supersede older disabled evidence.

## Impact
Usage persistence, subscription routing and login verification; no schema migration or fabricated quota.
