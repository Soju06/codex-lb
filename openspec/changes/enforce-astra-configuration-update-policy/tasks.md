## 1. Policy

- [x] 1.1 Validate configuration_update items against allowed/enforced efforts
- [x] 1.2 Prepend an allowed leading update on anchored restricted-key continuations
- [x] 1.3 Reject unsupported Astra update shapes, adjacent updates, compact, auto truncation/compaction
- [x] 1.4 Map Ultra to Max only at subscription to_payload serialization

## 2. Call sites

- [x] 2.1 Subscription HTTP stream/collect/compact/chat-completions
- [x] 2.2 Source Responses: key policy without Astra schema restrictions
- [x] 2.3 HTTP-bridge prepare uses payload.input after preparation

## 3. Verification

- [x] 3.1 Unit and integration regressions for bypass, continuation reset, Ultra identity, source contract
- [x] 3.2 Strict OpenSpec validation of this change
- [x] 3.3 Refresh stored client-plane update efforts after each injected-anchor continuation
- [x] 3.4 Preserve original HTTP full-resend bookkeeping while validating the trimmed continuation; prove streaming and collect routes retain durable prefix matching on a later resend
