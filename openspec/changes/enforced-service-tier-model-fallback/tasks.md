## 1. Selection

- [x] 1.1 Capture whether the client supplied a service tier before API-key enforcement mutates the request
- [x] 1.2 Drop an enforced tier from an account-routed request when the model's catalog never advertises it
- [x] 1.3 Keep rejecting an explicitly requested unadvertised tier, including on the quota-override path
- [x] 1.4 Preserve enforced tiers on source-routed and account-catalog-unknown models
- [x] 1.5 Propagate the effective tier through bridge compatibility, accounting, logging, and upstream forwarding

## 2. Diagnostics

- [x] 2.1 Name the service tier in the selection error when the tier excluded the accounts

## 3. Verification

- [x] 3.1 Cover an enforced tier on a model that advertises no tiers, proven failing before the fix
- [x] 3.2 Cover the same configuration driven from an `ApiKeyData` with `enforced_service_tier`
- [x] 3.3 Cover an advertised-but-unheld tier still failing, with the tier named in the message
- [x] 3.4 Cover explicit equal/alias tiers, bridge reuse, API-key accounting/logging, wire omission, and source routing
- [x] 3.5 Align the Responses API enforcement contract with account-catalog fallback semantics
