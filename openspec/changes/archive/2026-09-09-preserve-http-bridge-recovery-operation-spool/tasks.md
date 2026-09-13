## 1. Specification

- [x] 1.1 Define the optional-versus-required operation-spool reset contract.
- [x] 1.2 Document the owner-fence ordering and non-goals in `context.md`.

## 2. Implementation

- [x] 2.1 Make `required=False` ignore reset refusal and exceptions while
      preserving strict required behavior.
- [x] 2.2 Move the ordinary anchored local-recovery reset before durable-session
      retirement/replacement.

## 3. Coverage

- [x] 3.1 Add HTTP Responses bridge regression coverage for original-session
      reset ordering and optional refusal/exception behavior.
- [x] 3.2 Preserve the existing required stale-anchor reset regression.

## 4. Verification

- [x] 4.1 Run focused HTTP bridge unit/integration tests, Ruff, formatting, type
      checking, proxy architecture checks, and `git diff --check`.
- [x] 4.2 Run strict OpenSpec validation when the CLI is available; validation
      passed with `pnpm --silent dlx @fission-ai/openspec@1.10.0 validate
      preserve-http-bridge-recovery-operation-spool --strict --no-interactive`.
