## 1. Shared gated-model mapping

- [ ] 1.1 Add a canonical gated-model mapping helper that resolves model IDs to additional-usage `limit_name` keys and display labels.
- [ ] 1.2 Seed the mapping for `gpt-5.3-codex-spark` and cover the registry behavior with tests.

## 2. Proxy selection and error handling

- [ ] 2.1 Pre-filter candidate accounts for mapped gated models using fresh persisted `additional_usage_history` rows before building selection states.
- [ ] 2.2 Fail closed for mapped gated models and propagate stable selection error codes through proxy responses.
- [ ] 2.3 Preserve existing `AccountState` and persisted account-status semantics while evaluating gated-model eligibility.

## 3. UI and verification

- [ ] 3.1 Render mapped additional quota labels on the Accounts page.
- [ ] 3.2 Update unit/integration coverage for gating, freshness, stable errors, and mapped labels.
- [ ] 3.3 Run focused tests plus full project verification for the touched paths.
