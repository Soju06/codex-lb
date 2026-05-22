## 1. Implementation

- [x] 1.1 Keep compact's first 401 same-account forced refresh retry.
- [x] 1.2 After the refreshed compact retry still returns 401, mark/exclude that account and continue to another eligible account.
- [x] 1.3 Remove compact HTTP 401 from the low-level same-contract retry status set.
- [x] 1.4 Apply repeated auth-401 failover to pre-visible stream, thread-goal, Codex control, transcription, file create/finalize, and websocket connect paths.

## 2. Tests

- [x] 2.1 Add integration coverage for repeated compact 401 failover to another account.
- [x] 2.2 Verify the existing compact refresh-and-retry success path still passes.
- [x] 2.3 Add integration coverage for repeated auth-401 failover on stream, thread-goal, Codex control, transcription, and file create/finalize paths.

## 3. Spec Delta

- [x] 3.1 Add a `responses-api-compat` requirement for compact 401 failover after forced refresh.
- [x] 3.2 Extend the requirement to pre-visible proxy auth-failover surfaces.
- [x] 3.3 Validate the OpenSpec change.
