# Design

## Format and cryptography

The UTF-8 JSON v1 envelope contains `format`, `version`, a scrypt KDF descriptor (`salt`, `n`, `r`, `p`), an AES-256-GCM descriptor (`nonce`), and base64 ciphertext. Canonical JSON for every envelope field except ciphertext is authenticated as AES-GCM associated data. Encryption completes before the response is built, so no output is exposed before GCM authentication is finalized. Salt and nonce are newly random for every export.

The decrypted v1 payload contains a version, creation timestamp, and an account array. Each record contains credentials (auth mode and tokens), stable upstream identity/workspace claims, and only alias, plan type, routing policy, limit-warmup enabled, and security-work authorization. Local row ids, statuses, usage, health, reset/block/delete state, assignments, bindings, settings, histories, caches, and encryption keys are excluded.

## API flow

Export accepts an optional account-id list; omission means all visible accounts and an empty list means zero accounts. Every selected record is validated and decrypted before encryption. A missing account or incomplete credential fails the request with a safe per-account error rather than producing a partial bundle.

Preflight and commit accept the bundle file and passphrase as bounded multipart fields. The route authenticates and verifies write permission before body processing. Both calls independently decrypt and validate the complete payload, so neither passphrase nor plaintext is retained server-side. Preflight returns masked identity, portable metadata, new/matching state, and an integrity token computed from the exact opaque upload. Commit requires that token, a `skip` or `replace` mode, and explicit replacement confirmation. It rejects a different upload before any write.

## Identity and persistence

Preflight and commit call the repository's existing account-slot identity matcher. Source-local ids are used only as non-authoritative id seeds for new rows and are never used to select a replacement. Commit validates every record and conflict decision first, then persists credentials and metadata together in one transaction and encrypts tokens with the destination `TokenEncryptor`. New rows and replacements whose destination lifecycle was `ACTIVE` enter a `PAUSED` pending-validation quarantine in that same transaction; the transaction also advances routing and selection invalidation versions. Existing non-active replacement lifecycle state is preserved.

Post-persistence validation uses only background-owned database sessions, including any usage-refresh singleflight work that can outlive the request waiter. A successful validation may restore an active-before-import slot through a credential-version compare-and-set, with reactivation and cache invalidation committed atomically. Failure, timeout, cancellation, or a lost compare-and-set leaves the quarantine durable. Validation never reactivates a destination that was already non-active before replacement.

## Failure modes

- Wrong passphrase, modified ciphertext/metadata, and corrupt data share a safe `invalid_account_bundle` response.
- Unsupported format/version has a distinct safe `unsupported_account_bundle` response.
- Declared, streamed, ciphertext, and decrypted plaintext limits fail with `payload_too_large` before persistence.
- Duplicate identities within a bundle, missing required identity, invalid metadata, and incomplete credentials fail complete validation before the first write.
- Audit records contain only counts, conflict mode, safe outcomes, and destination ids; logs and responses never include credentials, passphrases, plaintext, or raw identities.

## Example operator flow

An operator selects two accounts, enters a passphrase twice, and downloads an opaque `.clb-account-bundle` file. On another installation, they select that file, enter the passphrase, inspect masked rows such as `a***@example.com`, choose `skip` or explicitly confirm `replace`, and receive imported/replaced/skipped/failed totals. The destination ciphertext is different because its at-rest key is used after bundle decryption.
