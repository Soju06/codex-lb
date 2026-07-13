## 1. Pricing registry

- [x] 1.1 Add canonical GPT-5.6 Sol, Terra, and Luna pricing for standard, Flex, Priority, and long-context usage
- [x] 1.2 Add the bare GPT-5.6-to-Sol alias and personality-specific wildcard aliases that win over the generic GPT-5 fallback

## 2. Regression coverage

- [x] 2.1 Add unit coverage for bare, exact, and suffixed GPT-5.6 pricing resolution
- [x] 2.2 Add unit coverage for GPT-5.6 standard, Flex, Priority, and long-context cost calculations
- [x] 2.3 Add API-key service coverage proving GPT-5.6 usage settles `cost_usd` quotas with personality-specific rates

## 3. Verification

- [x] 3.1 Run the focused pricing and API-key service tests
- [x] 3.2 Run lint/type checks for the changed Python files and validate the OpenSpec change
