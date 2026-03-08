## 1. Implementation

- [x] 1.1 Update `/v1/responses` non-streaming SSE collection to retain `response.output_item.*` payloads and merge them into the terminal response when `output` is missing or empty

## 2. Tests

- [x] 2.1 Add an integration regression test covering reasoning-style output item preservation for non-streaming `/v1/responses`

## 3. Spec Delta

- [x] 3.1 Add a `responses-api-compat` spec delta for non-streaming output reconstruction
- [x] 3.2 Validate specs locally with `openspec validate --specs`
