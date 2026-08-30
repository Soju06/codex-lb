## ADDED Requirements

### Requirement: Portable account bundles are encrypted, bounded, and complete

The system MUST export zero, one, all, or an explicit subset of accounts in a documented v1 envelope encrypted using scrypt and AES-256-GCM with fresh salt and nonce and authenticated envelope metadata. It MUST include only usable credentials, stable identity/workspace claims, alias, plan type, routing policy, limit-warmup enabled, and security-work authorization. It MUST exclude installation encryption material and destination-local or transient account state. Missing or incomplete selected credentials MUST fail safely per account and MUST NOT be silently omitted. Export MUST authenticate and require write access before reading or parsing its request body. The configured account-bundle byte limit MUST be enforced against declared, streamed, encrypted, and decrypted sizes; a streamed request chunk that would make the accumulated body exceed the limit MUST be rejected before that chunk is appended to or retained by the body buffer.

#### Scenario: Bundle moves between different installation keys

- **WHEN** an operator exports selected accounts with a passphrase and imports the bundle into an installation with a different at-rest encryption key
- **THEN** the selected credentials and portable metadata are restored
- **AND** destination credential ciphertext is encrypted with the destination key
- **AND** excluded source-local state is not restored

### Requirement: Bundle import validates before writing and uses existing identity semantics

Import preflight and commit MUST authenticate and require write access before body processing. Each operation MUST independently decrypt and validate the complete upload, account count, required identities, credentials, metadata, and size before the first write. Preflight MUST return only masked identities, portable metadata, new/matching state, and an integrity token for the exact opaque upload. It MUST retain neither passphrase nor plaintext. Commit MUST require the same upload and integrity token, support `skip` and `replace`, require explicit replace confirmation, ignore source row ids as destination authority, and match accounts using the existing account-slot identity semantics. Skip re-import MUST be idempotent.

Bundle duplicate detection MUST lowercase email without changing any other characters and MUST define the workspace-slot key as `workspace_id` when non-null, otherwise `workspace_label` when non-null, otherwise no workspace key; equal non-null bundle keys from either field are equivalent across destination columns, and multiple equivalent destination candidates MUST fail as an identity conflict. This portable cross-column equivalence MUST apply only to bundle matching; ordinary single-account `auth.json` import MUST retain its legacy exact-column workspace matching. Each duplicate-detection pass MUST run in O(N) time over no more than N = 10,000 source records. A payload exceeding that bound MUST fail as `invalid_account_bundle` before any write, and a duplicate or two records targeting the same destination MUST also fail without writing. Destination email fallback MUST use the predicate `lower(accounts.email) = lowercased_source_email`, backed by the `idx_accounts_email_lower` expression index on `accounts (lower(email))`. PostgreSQL commit MUST serialize with ordinary imports using the configured merge-email behavior and identity membership locks, recheck no more than N candidate memberships per lock attempt, retry the whole lock acquisition at most once if membership changed, and fail without writing if membership is unstable after the second attempt. The bundle batch MUST commit atomically. Replace MUST update only credentials, portable metadata, and stable identity fields while preserving destination-local lifecycle and transient routing state.

#### Scenario: Replace requires exact confirmed upload

- **GIVEN** preflight reports a matching destination account
- **WHEN** commit uses replace mode without explicit confirmation or with a different upload
- **THEN** no account is written
- **AND** the system returns a safe conflict or validation error

#### Scenario: Complete validation precedes persistence

- **GIVEN** a bundle contains valid records followed by an invalid record
- **WHEN** it is preflighted or committed
- **THEN** no account record is changed

### Requirement: Bundle operations protect secrets and preserve existing single-account flows

Bundle export, preflight, commit, and failures MUST be audited with only counts, mode, safe outcomes, and established destination ids. Responses, logs, audit details, and UI MUST NOT expose passphrases, decrypted payloads, tokens, or raw identities. Wrong-passphrase, corruption, malformed data, unsupported format/version, and oversize inputs MUST return safe errors. Export responses MUST be attachments with no-store headers and import responses MUST be no-store. Existing single-account `auth.json` import and selected-account export APIs and UI MUST remain distinct and unchanged.

Every bundle response, including parsing, validation, unsupported-encoding, and oversize failures, MUST carry no-store/no-cache headers. The atomic credential commit MUST quarantine every new slot and every replacement whose prior status was `ACTIVE`, `RATE_LIMITED`, or `QUOTA_EXCEEDED` as durably routing-unavailable and publish routing/selection invalidation in the same transaction. A replacement already `PAUSED`, `REAUTH_REQUIRED`, or `DEACTIVATED` MUST preserve its lifecycle. After commit, every non-skipped account MUST receive a bounded, proxy-aware best-effort validation/usage refresh using only independently owned database sessions and secret-redacted logging. Bundle validation MUST suppress usage-derived metadata, lifecycle-status, and routing mutations while preserving guarded credential rotation. After successful validation of the exact committed credential version, a newly imported slot MAY be activated and a quarantined replacement MAY be restored to its exact prior status, deactivation reason, reset time, and blocked time. Activation or restoration MUST compare against the credential version actually used by successful validation, including a guarded rotation used for a successful retry, and MUST publish routing/selection invalidation atomically. A concurrent unrelated credential replacement that validation did not use MUST cause that compare-and-set to miss and MUST NOT be activated by the validation. Bundle validation MUST NOT activate a replacement that was not previously active. A refresh failure, timeout, cancellation, or lost compare-and-set MUST NOT roll back a committed row, MUST return only fixed warnings when the request remains connected, and MUST leave any slot quarantined by commit durably paused and unroutable; an already unavailable replacement MUST retain its destination lifecycle. The export dialog MUST honor privacy mode for email-derived labels, and bundle passphrase inputs MUST NOT identify the secret as the dashboard login password. The import dialog MUST clear replace mode and confirmation when returning to file selection or starting another preflight, MUST present a successful commit independently of later account-query refresh failure, and MUST clear the committed passphrase. Dialog selection MUST reconcile asynchronous account loading without undoing deliberate edits, and late operation completions after close/reopen MUST NOT restore sensitive state.

#### Scenario: Concurrent unvalidated replacement loses restoration

- **GIVEN** post-commit validation succeeds with one credential version, including any guarded rotation used by its successful retry
- **AND** an unrelated credential replacement commits before lifecycle restoration
- **WHEN** restoration compares the validated credential version with the current row
- **THEN** the compare-and-set misses and the unrelated replacement remains unroutable

#### Scenario: Successful validation activates only eligible slots

- **GIVEN** a newly imported slot, an active-before-import replacement, a rate-limited replacement, and an already paused replacement have committed valid credentials
- **WHEN** validation succeeds for each exact committed credential version
- **THEN** the new slot is activated, the active-before-import replacement is reactivated, and the rate-limited replacement is restored to its prior lifecycle with atomic invalidation
- **AND** the already paused replacement preserves its destination lifecycle and is not activated

#### Scenario: Wrong passphrase is secret-free

- **WHEN** an operator submits the wrong passphrase
- **THEN** import fails without a write
- **AND** no response, log, or audit record contains the passphrase, ciphertext plaintext, tokens, or raw identity
