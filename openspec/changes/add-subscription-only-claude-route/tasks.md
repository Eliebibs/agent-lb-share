## 1. Implementation
- [x] 1.1 Add subscription policy and messages/count-tokens routes.
- [x] 1.2 Enforce OAuth-only credentials, Claude models and disabled extra usage.
- [x] 1.3 Bound authentication failover for token counting.
- [x] 1.4 Expose successful OAuth account identity without secrets.
- [x] 1.5 Remove API-key creation scope from subscription-only login and support cancellation.

## 2. Validation
- [x] 2.1 Exercise route-level policy, exclusions, failover and compatibility tests.
- [x] 2.2 Run lint, imports, strict OpenSpec validation and isolated runtime smoke.
