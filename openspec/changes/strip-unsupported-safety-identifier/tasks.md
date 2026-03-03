## 1. Payload Sanitization

- [x] 1.1 Add `safety_identifier` to Responses unsupported-upstream field stripping list
- [x] 1.2 Ensure stripping applies to both standard and compact Responses payload builders

## 2. Tests

- [x] 2.1 Extend Responses payload unit test to assert `safety_identifier` is removed
- [x] 2.2 Extend compact Responses payload unit test to assert `safety_identifier` is removed
- [x] 2.3 Add chat-mapping regression assertion for `safety_identifier`

## 3. Specs

- [x] 3.1 Add `responses-api-compat` spec delta for `safety_identifier` stripping
- [x] 3.2 Run `openspec validate --specs`
