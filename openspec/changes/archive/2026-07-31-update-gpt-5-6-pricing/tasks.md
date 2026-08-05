## 1. Contract

- [x] 1.1 Define the current GPT-5.6 standard, Fast/priority, Flex, and standard long-context rates under `api-keys`.
- [x] 1.2 Document that batch and cache-write pricing are not part of the proxy's cost-accounting contract.

## 2. Regression coverage

- [x] 2.1 Update direct GPT-5.6 standard, Fast/priority, Flex, and long-context totals.
- [x] 2.2 Update the API-key cost reservation and finalization expectations for Terra and Luna aliases.
- [x] 2.3 Preserve the exact 272,000-token boundary assertion.

## 3. Implementation

- [x] 3.1 Update the bundled Terra and Luna `ModelPrice` entries without changing the pricing algorithm or aliases.

## 4. Verification

- [x] 4.1 Run the focused pricing and API-key service tests.
- [x] 4.2 Run Ruff and strict OpenSpec validation.
- [x] 4.3 Archive the verified change and validate the resulting main spec.
