# Design

## Format and cryptography

The UTF-8 JSON v1 envelope contains `format`, `version`, a scrypt KDF descriptor (`salt`, `n`, `r`, `p`), an AES-256-GCM descriptor (`nonce`), and base64 ciphertext. Canonical JSON for every envelope field except ciphertext is authenticated as AES-GCM associated data. Encryption completes before the response is built, so no output is exposed before GCM authentication is finalized. Salt and nonce are newly random for every export.

The decrypted v1 payload contains a version, creation timestamp, and an account array. Each record contains credentials (auth mode and tokens), stable upstream identity/workspace claims, and only alias, plan type, routing policy, limit-warmup enabled, and security-work authorization. Local row ids, statuses, usage, health, reset/block/delete state, assignments, bindings, settings, histories, caches, and encryption keys are excluded.

## API flow

Export accepts an optional account-id list; omission means all visible accounts and an empty list means zero accounts. Every selected record is validated and decrypted before encryption. A missing account or incomplete credential fails the request with a safe per-account error rather than producing a partial bundle.

Export parses its bounded JSON request only after authentication and write authorization. Its streaming parser checks the accumulated byte count plus the next chunk before retaining that chunk, so one oversized chunk is rejected without first growing the request buffer past the configured limit. Preflight and commit accept the bundle file and passphrase as bounded multipart fields after the same authorization gate. Both import calls independently decrypt and validate the complete payload, so neither passphrase nor plaintext is retained server-side. Preflight returns masked identity, portable metadata, new/matching state, and an integrity token computed from the exact opaque upload. Commit requires that token, a `skip` or `replace` mode, and explicit replacement confirmation. It rejects a different upload before any write.

## Identity and persistence

Bundle preflight and commit apply portable account-slot matching on top of the established import fallbacks. Source-local ids are used only as non-authoritative id seeds for new rows and are never used to select a replacement. For bundles, email matching lowercases without trimming, and `workspace_id` takes precedence over the equivalent `workspace_label` slot key; equal non-null bundle keys may match across those columns, and ambiguous equivalent destinations fail closed. Ordinary single-account `auth.json` import retains its legacy exact-column workspace matching and does not gain bundle cross-column equivalence. Commit validates every record and conflict decision first, then persists credentials and metadata together in one transaction and encrypts tokens with the destination `TokenEncryptor`. New rows and replacements whose destination lifecycle is not durably routing-unavailable enter a `PAUSED` pending-validation quarantine in that same transaction; this includes `ACTIVE`, `REAUTH_REQUIRED`, `RATE_LIMITED`, and `QUOTA_EXCEEDED`, and the transaction also advances routing and selection invalidation versions. Existing `PAUSED` and `DEACTIVATED` replacement lifecycle state is preserved. Successful exact-version validation restores a quarantined `REAUTH_REQUIRED` replacement to that prior lifecycle before its routing-unavailable mark is cleared.

Post-persistence validation uses only background-owned database sessions, secret-redacted log paths, and any usage-refresh singleflight work that can outlive the request waiter. The fixed batch validation budget is divided across non-skipped records so every record receives a fair bounded opportunity. A timed-out singleflight retains the commit's one sequential validation slot until its work finishes; later records spend only their reserved opportunity waiting for that slot and remain quarantined if it is unavailable. This prevents one commit from accumulating waiter-detached refreshes without extending its fixed response budget. During the validation refresh, bundle-mode usage handling suppresses usage-derived metadata and usage-refresh lifecycle-status or routing mutations while retaining AuthManager's guarded credential rotation. This suppression does not apply to mandatory post-validation activation, restoration, or routing/selection invalidation. Successful exact-version validation activates an eligible newly imported slot or restores an eligible quarantined replacement's exact prior lifecycle through a credential-version compare-and-set, with restoration and cache invalidation committed atomically. The compare-and-set uses the credential version actually validated, including a guarded rotation used for a successful retry; it never adopts a later unrelated repository replacement through a generic post-validation resync. Failure, timeout, cancellation, or a lost compare-and-set leaves any commit-quarantined credential in a durable paused and routing-unavailable quarantine; already unavailable replacements retain their destination lifecycle. Validation never activates a destination that was already non-active before replacement.

## Dashboard lifecycle

Returning from preview or starting a new preflight clears replace mode and its confirmation so approval is tied to the reviewed upload. Once commit returns successfully, the dialog clears the passphrase and presents the durable result before refreshing account queries; a refresh failure cannot turn the completed import into a retryable error. Export account labels reuse the dashboard privacy rule, and bundle passphrase fields use a non-login autocomplete purpose.

## Failure modes

- Wrong passphrase, modified ciphertext/metadata, and corrupt data share a safe `invalid_account_bundle` response.
- Unsupported format/version has a distinct safe `unsupported_account_bundle` response.
- Declared, streamed, ciphertext, and decrypted plaintext limits fail with `payload_too_large` before persistence; streamed chunks are rejected before retention when accumulated size plus the chunk exceeds the limit.
- Duplicate identities within a bundle, missing required identity, invalid metadata, and incomplete credentials fail complete validation before the first write.
- Audit records contain only counts, conflict mode, safe outcomes, and destination ids; logs and responses never include credentials, passphrases, plaintext, or raw identities.

## Example operator flow

An operator selects two accounts, enters a passphrase twice, and downloads an opaque `.clb-account-bundle` file. On another installation, they select that file, enter the passphrase, inspect masked rows such as `a***@example.com`, choose `skip` or explicitly confirm `replace`, and receive imported/replaced/skipped/failed totals. The destination ciphertext is different because its at-rest key is used after bundle decryption.
