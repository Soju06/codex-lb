## ADDED Requirements

### Requirement: Account bundle documentation describes safe operation and limits

The generated settings reference MUST list `CODEX_LB_ACCOUNT_BUNDLE_MAX_BYTES`, its 8 MiB default, and its role in bounding encrypted uploads and decrypted account-bundle payloads. The authentication documentation MUST describe the encrypted export/import workflow, unrecoverable passphrases, conflict choices, excluded installation-local data, and the distinct existing auth.json flows, and MUST link the `account-bundles` capability.

#### Scenario: Operator finds bundle security and limit guidance

- **WHEN** an operator reads authentication and settings documentation before moving accounts
- **THEN** they can identify the sensitive-file warning, passphrase recovery limitation, skip/replace behavior, excluded state, and configured byte limit
