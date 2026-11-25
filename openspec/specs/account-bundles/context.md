# Account bundle context

Normative requirements live in [`spec.md`](./spec.md).

## Purpose and scope

Account bundles are an operator-controlled migration and backup mechanism for accounts only. They intentionally do not clone an installation, its histories, assignments, bindings, health, settings, or encryption keys.

## Decisions and constraints

- The v1 JSON envelope uses scrypt and AES-256-GCM from the existing `cryptography` dependency. Canonical envelope metadata is authenticated as associated data.
- The passphrase and decrypted JSON live only within one export, preflight, or commit request. Preflight identifies the exact opaque upload with SHA-256 rather than retaining plaintext or passphrases.
- The 8 MiB `CODEX_LB_ACCOUNT_BUNDLE_MAX_BYTES` default bounds both opaque and decrypted bytes. It works with zero configuration while remaining adjustable for unusually large pools or stricter ingress budgets.
- Destination matching delegates to the account repository's slot matcher. A source row id is not part of the portable schema and cannot choose a destination row.

## Failure modes

Wrong passphrases and authenticated-data/ciphertext corruption use the same safe invalid-bundle error. Unsupported versions are distinguished without disclosing decrypted data. All records are validated before persistence, and commit writes in one transaction; post-persistence validation can warn but cannot relabel committed credentials as rolled back.

## Example

Exporting `a@example.com` and `b@example.com` yields one opaque `.clb-account-bundle` download. A destination with a different `encryption.key` preflights masked rows (`a***@example.com`), skips or replaces matches as selected, and stores new Fernet ciphertext produced by its own at-rest key.
