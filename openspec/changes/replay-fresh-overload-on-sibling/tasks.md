## 1. Regression coverage

- [x] 1.1 Reproduce a fresh HTTP stream receiving `server_is_overloaded` or
  `overloaded_error` as either the first SSE event or after only
  `response.created`, then succeeding on excluded-account sibling B with
  live-effective deterministic failover.
- [x] 1.2 Prove previous-response ownership, disabled deterministic failover,
  emitted content/tool output, and terminal output evidence remain fail closed.

## 2. Stream routing

- [x] 2.1 Defer only the replay-safe lifecycle prelude at the direct HTTP SSE
  boundary and return its output-free overload terminal to the existing retry
  owner.
- [x] 2.2 Retain the existing health, lease, exclusion, and overload-backoff
  handling.

## 3. Verification

- [x] 3.1 Run the focused stream retry regression and the proxy architecture
  guard.
- [x] 3.2 Validate this OpenSpec change strictly. Repository-wide spec validation remains a pre-existing baseline failure outside this delta.
