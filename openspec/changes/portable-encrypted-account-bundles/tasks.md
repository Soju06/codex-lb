## 1. Contract and cryptography

- [x] 1.1 Define and test the bounded v1 envelope/payload schemas, scrypt derivation, AES-256-GCM encryption, authenticated metadata, and safe errors.
- [x] 1.2 Add the conservative configurable bundle/upload/plaintext size limit and document its rationale.

## 2. Backend workflow

- [x] 2.1 Add authorized export, preflight, and commit endpoints with no-store/attachment headers and secret-free audits.
- [x] 2.2 Reuse account-slot identity matching and add validation-before-write skip/replace persistence with destination encryption and explicit confirmation.
- [x] 2.3 Add focused backend and distinct-key round-trip coverage without network calls or real credentials.

## 3. Dashboard workflow

- [x] 3.1 Add the export action/dialog with default-all selection, passphrase confirmation, warning, download, and sensitive-state clearing.
- [x] 3.2 Add a distinct bundle-import chooser card and preflight/commit wizard with masked preview, conflicts, confirmation, results, clearing, and query invalidation.
- [x] 3.3 Add English, Korean, and Chinese strings and focused frontend coverage while preserving the existing auth.json dialogs.

## 4. Documentation and verification

- [x] 4.1 Update the owning main specs/context and user-facing authentication/settings reference.
- [x] 4.2 Run focused backend/frontend tests, lint/type/build checks, and strict OpenSpec validation that are available locally.

## 5. Independent audit hardening

- [x] 5.1 Redact all bundle error/audit paths and apply no-store headers to early failures.
- [x] 5.2 Preserve destination-local lifecycle/routing state during replacement and avoid post-commit refresh failures.
- [x] 5.3 Serialize PostgreSQL bundle batches with merge-email/identity locks, membership rechecks, and locked duplicate rejection.
- [x] 5.4 Perform bounded proxy-aware best-effort validation with fixed warnings after atomic commit.
- [x] 5.5 Normalize duplicate workspace-slot/email identities across payload, preflight, and commit.
- [x] 5.6 Reconcile asynchronous export selection and guard dialog close/reopen races.
