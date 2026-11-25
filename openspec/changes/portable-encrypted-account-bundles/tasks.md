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
- [x] 5.7 Keep duplicate validation bounded at the maximum account count and index normalized destination-email lookups on both database backends.

## 6. Post-review remediation

- [x] 6.1 Quarantine every potentially routable replacement durably, restore its exact prior lifecycle only after exact-credential validation, and preserve already unavailable lifecycle states.
- [x] 6.2 Authenticate and authorize bundle export before bounded manual body parsing, and redact all post-commit validation log details.
- [x] 6.3 Match equivalent destination `workspace_id` and `workspace_label` slot keys across columns.
- [x] 6.4 Bind replace confirmation to one preview, present durable commit success independently of refresh, honor privacy mode in export labels, and mark bundle passphrases as non-login credentials.
- [x] 6.5 Add focused backend/frontend regressions and rerun the full applicable verification matrix.
- [x] 6.6 Bind restoration to the exact credential version used by successful validation, including guarded token rotation, without adopting an unrelated concurrent replacement.
- [x] 6.7 Reject an oversized export request chunk before appending or retaining it, and preserve legacy ordinary-import matching outside the bundle path.
- [x] 6.8 Restrict bundle validation refreshes to guarded token-only rotation, suppress metadata/status/routing writes, and keep failed credentials quarantined.
