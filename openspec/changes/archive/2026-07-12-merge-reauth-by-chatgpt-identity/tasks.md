## 1. OpenSpec And Coverage

- [x] 1.1 Add an OpenSpec change describing reauth merge-by-ChatGPT-identity behavior.
- [x] 1.2 Add regression coverage proving concurrent reauth persists one local row for the same upstream ChatGPT identity.

## 2. Implementation

- [x] 2.1 Route OAuth token persistence through identity-based account upsert.
- [x] 2.2 Serialize identity-merge persistence so concurrent completions cannot allocate duplicate local ids.
- [x] 2.3 Ensure duplicate same-identity rows are reconciled by repointing dependent tables before deleting obsolete rows.

## 3. Verification

- [x] 3.1 Run the targeted account repository tests.
- [x] 3.2 Validate the OpenSpec change.
