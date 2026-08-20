## 1. Implementation

- [x] 1.1 Require exact account-row proof for direct internal account IDs.
- [x] 1.2 Resolve unknown or suffixed internal IDs only through a unique supplied ChatGPT account identity.
- [x] 1.3 Drop unresolved or ambiguous snapshots without failing the serving path.
- [x] 1.4 Coalesce duplicate snapshots under the normalized account ID after resolution.
- [x] 1.5 Revalidate cached exact account IDs and bypass stale raw aliases when a ChatGPT identity is supplied.
- [x] 1.6 Add an indexed, bounded ChatGPT identity lookup path.

## 2. Regression Coverage

- [x] 2.1 Cover the proxy hub path that publishes both a raw internal ID and a ChatGPT identity.
- [x] 2.2 Cover that an unproven suffixed internal ID is dropped instead of guessed.
- [x] 2.3 Cover duplicate coalescing after a raw ID is resolved to the stored account.
- [x] 2.4 Cover moved ChatGPT identities, conflicting supplied identities, and deleted cached exact IDs.

## 3. Validation

- [x] 3.1 Run focused live usage ingest tests.
- [x] 3.2 Run Ruff on changed Python files.
- [x] 3.3 Run strict OpenSpec validation.
- [x] 3.4 Run migration policy and schema drift validation.
