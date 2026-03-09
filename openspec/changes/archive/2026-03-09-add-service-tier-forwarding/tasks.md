## 1. Request models

- [x] 1.1 Add explicit `service_tier` fields to shared Responses request models
- [x] 1.2 Ensure Chat Completions mapping preserves `service_tier` into Responses payloads

## 2. Regression coverage

- [x] 2.1 Add unit tests for Responses and v1 Responses serialization of `service_tier`
- [x] 2.2 Add unit test for Chat Completions mapping of `service_tier`
- [x] 2.3 Add integration tests proving proxy forwarding for backend Responses and Chat Completions

## 3. Spec updates

- [x] 3.1 Add Responses API requirement for forwarding `service_tier`
- [x] 3.2 Add Chat Completions requirement for preserving `service_tier` in Responses mapping
