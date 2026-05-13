## 1. Implementation

- [x] 1.1 Strip request-side `service_tier: "default"` from Responses payloads before upstream forwarding.
- [x] 1.2 Keep `fast` canonicalization and literal `ultrafast` preservation intact.

## 2. Verification

- [x] 2.1 Add unit coverage for standard, v1, and compact Responses payload serialization with `service_tier: "default"`.
- [x] 2.2 Add integration coverage proving `/backend-api/codex/responses` does not forward request-side `default` upstream.
- [x] 2.3 Run focused tests and validate OpenSpec artifacts.
