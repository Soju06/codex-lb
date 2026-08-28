## ADDED Requirements

### Requirement: Portable account bundles are encrypted, bounded, and complete

The system MUST export zero, one, all, or an explicit subset of accounts in a documented v1 envelope encrypted using scrypt and AES-256-GCM with fresh salt and nonce and authenticated envelope metadata. It MUST include only usable credentials, stable identity/workspace claims, alias, plan type, routing policy, limit-warmup enabled, and security-work authorization. It MUST exclude installation encryption material and destination-local or transient account state. Missing or incomplete selected credentials MUST fail safely per account and MUST NOT be silently omitted. The configured account-bundle byte limit MUST be enforced against declared, streamed, encrypted, and decrypted sizes.

#### Scenario: Bundle moves between different installation keys

- **WHEN** an operator exports selected accounts with a passphrase and imports the bundle into an installation with a different at-rest encryption key
- **THEN** the selected credentials and portable metadata are restored
- **AND** destination credential ciphertext is encrypted with the destination key
- **AND** excluded source-local state is not restored

### Requirement: Bundle import validates before writing and uses existing identity semantics

Import preflight and commit MUST authenticate and require write access before body processing. Each operation MUST independently decrypt and validate the complete upload, account count, required identities, credentials, metadata, and size before the first write. Preflight MUST return only masked identities, portable metadata, new/matching state, and an integrity token for the exact opaque upload. It MUST retain neither passphrase nor plaintext. Commit MUST require the same upload and integrity token, support `skip` and `replace`, require explicit replace confirmation, ignore source row ids as destination authority, and match accounts using the existing account-slot identity semantics. Skip re-import MUST be idempotent.

Duplicate detection MUST normalize email fallback and workspace-slot equivalence, including equivalent `workspace_id` and `workspace_label` values. PostgreSQL commit MUST serialize with ordinary imports using configured merge-email behavior, identity locks, and bounded membership recheck. Replace MUST preserve destination-local lifecycle and transient routing state.

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

Every bundle response, including parsing, validation, unsupported-encoding, and oversize failures, MUST carry no-store/no-cache headers. Post-commit validation MUST be bounded, proxy-aware, best effort, and secret-free; failure MUST NOT undo committed rows and MUST leave unusable credentials unroutable. Dialog selection MUST reconcile asynchronous account loading without undoing deliberate edits, and late operation completions after close/reopen MUST NOT restore sensitive state.

#### Scenario: Wrong passphrase is secret-free

- **WHEN** an operator submits the wrong passphrase
- **THEN** import fails without a write
- **AND** no response, log, or audit record contains the passphrase, ciphertext plaintext, tokens, or raw identity
