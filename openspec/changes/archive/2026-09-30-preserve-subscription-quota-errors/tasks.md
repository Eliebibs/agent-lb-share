## 1. Implementation

- [x] 1.1 Preserve upstream `429` classification after strict-route candidate exhaustion.
- [x] 1.2 Keep all-auth-rejected strict requests mapped to the guarded login-required `403`.
- [x] 1.3 Add an externally visible subscription Messages regression test.

## 2. Verification

- [x] 2.1 Run the targeted subscription proxy integration tests.
- [x] 2.2 Run Ruff on the changed Python files.
- [x] 2.3 Validate the OpenSpec change strictly.
