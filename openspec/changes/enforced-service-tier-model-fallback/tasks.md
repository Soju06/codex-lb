## 1. Selection

- [x] 1.1 Distinguish an API-key-enforced service tier from a client-requested one at account selection
- [x] 1.2 Drop an enforced tier that the model's catalog never advertises, so the model routes at its default tier
- [x] 1.3 Keep rejecting an explicitly requested unadvertised tier, including on the quota-override path
- [x] 1.4 Key the selection-inputs cache on tier origin so enforced and requested tiers cannot share an entry

## 2. Diagnostics

- [x] 2.1 Name the service tier in the selection error when the tier excluded the accounts

## 3. Verification

- [x] 3.1 Cover an enforced tier on a model that advertises no tiers, proven failing before the fix
- [x] 3.2 Cover the same configuration driven from an `ApiKeyData` with `enforced_service_tier`
- [x] 3.3 Cover an advertised-but-unheld tier still failing, with the tier named in the message
