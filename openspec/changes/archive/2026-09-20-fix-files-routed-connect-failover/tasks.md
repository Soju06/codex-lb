## 1. Routed transport regression

- [x] 1.1 Exercise the actual file-create route through CodexClient and demonstrate pre-dispatch connection failure bypasses eligible fallback.
- [x] 1.2 Cover ambiguous request, body-read, TLS, process-network, and pinned-owner fail-closed controls.

## 2. Implementation

- [x] 2.1 Preserve typed file transport metadata and apply the existing unary retry policy without text-based replay of typed failures.
- [x] 2.2 Verify successful fallback pins the file to the completing account.

## 3. Verification

- [x] 3.1 Run focused route/client and unary regression tests, lint, format, type checks, and strict OpenSpec validation.
- [x] 3.2 Attempt the repository full local-ci gate and record any pre-existing blockers accurately.
- [x] 3.3 Review requirement coverage, synchronize stable specs/context, and archive the verified change.
