# account-bundles Specification

## Purpose

Portable, passphrase-encrypted multi-account backup and restore between codex-lb installations.

## Requirements

### Requirement: Portable account bundles are encrypted, bounded, and complete

The system MUST export zero, one, all, or an explicit subset of accounts in a documented v1 envelope encrypted using scrypt and AES-256-GCM with fresh salt and nonce and authenticated envelope metadata. It MUST include only usable credentials, stable identity/workspace claims, alias, plan type, routing policy, limit-warmup enabled, and security-work authorization. It MUST exclude installation encryption material and destination-local or transient account state. Missing or incomplete selected credentials MUST fail safely per account and MUST NOT be silently omitted. The configured account-bundle byte limit MUST be enforced against declared, streamed, encrypted, and decrypted sizes.

#### Scenario: Bundle moves between different installation keys

- **WHEN** an operator exports selected accounts with a passphrase and imports the bundle into an installation with a different at-rest encryption key
- **THEN** the selected credentials and portable metadata are restored with destination encryption
- **AND** excluded source-local state is not restored

### Requirement: Bundle import validates before writing and uses existing identity semantics

Import preflight and commit MUST authenticate and require write access before body processing. Each operation MUST independently decrypt and validate the complete upload, account count, required identities, credentials, metadata, and size before the first write. Preflight MUST return only masked identities, portable metadata, new/matching state, and an integrity token for the exact opaque upload. Commit MUST require that upload and token, support idempotent `skip` and explicit-confirmation `replace`, ignore source row ids as destination authority, and match accounts with the existing account-slot semantics.

Bundle duplicate detection MUST use normalized email fallback and workspace-slot equivalence, including equivalent `workspace_id` and `workspace_label` values, and MUST remain bounded at the maximum bundle account count. The database schema MUST provide an expression index matching the normalized destination-email lookup. PostgreSQL commit MUST serialize with ordinary imports using the configured merge-email behavior, identity membership locks and bounded membership recheck. The bundle batch MUST commit atomically and MUST reject two records targeting the same destination while locked. Replace MUST update only credentials, portable metadata, and stable identity fields while preserving destination-local lifecycle and transient routing state.

#### Scenario: Complete validation precedes persistence

- **GIVEN** a bundle contains valid records followed by an invalid record
- **WHEN** it is preflighted or committed
- **THEN** no account record is changed

### Requirement: Bundle operations protect secrets and preserve single-account flows

Bundle operations and failures MUST be audited with only counts, mode, safe outcomes, and established destination ids. Responses, logs, audit details, and UI MUST NOT expose passphrases, decrypted payloads, tokens, or raw identities. Existing single-account `auth.json` import and selected-account export APIs and UI MUST remain distinct and unchanged.

Every bundle response, including framework parsing, validation, encoding, and size failures, MUST carry no-store/no-cache headers. The atomic credential commit MUST quarantine every new or previously active slot as durably routing-unavailable and MUST publish routing/selection invalidation in the same transaction; a non-active replacement MUST preserve its destination lifecycle. After commit, every non-skipped account MUST receive a bounded, proxy-aware best-effort validation/usage refresh using only independently owned database sessions. Only a slot that was active before quarantine MAY be reactivated after successful validation, and reactivation MUST compare the validated credential version and publish invalidation atomically. A refresh failure, timeout, cancellation, or lost compare-and-set MUST NOT roll back a committed row, MUST return only fixed warnings when the request remains connected, and MUST leave the changed credentials unroutable.

The export dialog MUST select all accounts when opened after asynchronous loading, prune removed accounts, and preserve deliberate selection changes while open. Closing and reopening either bundle dialog during an operation MUST clear sensitive state and MUST prevent late completion from restoring prior file, passphrase, preview, result, or error state.

#### Scenario: Wrong passphrase is secret-free

- **WHEN** an operator submits the wrong passphrase
- **THEN** import fails without a write or secret-bearing response, log, or audit detail
