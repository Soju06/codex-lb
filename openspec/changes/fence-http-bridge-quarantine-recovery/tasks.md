# Tasks

## 1. Spec

- [x] 1.1 Add a `responses-api-compat` delta that permits durable quarantine recovery on completed replacement responses.
- [x] 1.2 Require generation fencing before durable mutation and renewal-snapshot validation before clearing quarantine.

## 2. Implementation

- [x] 2.1 Thread the captured quarantine generation into durable recovery.
- [x] 2.2 Reject stale generations before durable rebind or renewal.
- [x] 2.3 Validate the renewal snapshot's session id, owner instance, owner epoch, account id, and response id before reporting recovery success.

## 3. Coverage

- [x] 3.1 Add a regression where a foreign renewal snapshot returns and quarantine recovery is not reported as successful.
- [x] 3.2 Add a regression where generation N+1 quarantine lands while generation N recovery is in flight, proving the newer owner/anchor survives.

## 4. Verification

- [ ] 4.1 Run the added tests and touched test module.
- [x] 4.2 Run `ruff check .`.
- [x] 4.3 Run OpenSpec validation.
