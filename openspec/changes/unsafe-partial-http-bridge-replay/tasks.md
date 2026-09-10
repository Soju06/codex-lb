## 1. Specification and settings

- [x] 1.1 Add the opt-in unsafe partial replay setting with a false default.
- [x] 1.2 Document the environment variable, risk, and one-shot semantics.

## 2. Implementation

- [x] 2.1 Reconstruct a bounded fresh root from durable completed turns and the
      interrupted request input.
- [x] 2.2 Reject ambiguous tool/side-effect and malformed transcript cases.
- [x] 2.3 Add a fenced, single-attempt operation rebind and one-shot retry
      authorization for partial responses.
- [x] 2.4 Keep existing safe/complete-transcript retry gates unchanged when the
      new setting is disabled.
- [x] 2.5 Add distinct recovery/rejection telemetry.

## 3. Verification

- [x] 3.1 Cover the default-off setting and the successful partial replay path.
- [x] 3.2 Cover tool-call rejection, missing transcript, rebind failure, and
      authorization consumption.
- [x] 3.3 Run focused tests, lint/type checks, and strict OpenSpec validation.
