## 1. Spec

- [x] 1.1 Add oversized `response.create` guard requirements for Responses HTTP and websocket flows
- [x] 1.2 Record change-level context for upstream websocket budget handling
- [x] 1.3 Validate OpenSpec changes

## 2. Tests

- [x] 2.1 Add HTTP bridge coverage for pre-upstream `payload_too_large`
- [x] 2.2 Add HTTP bridge coverage for historical inline-artifact slimming
- [x] 2.3 Add websocket coverage for pre-upstream `payload_too_large`
- [x] 2.4 Add websocket coverage for historical inline-artifact slimming
- [x] 2.5 Add unit coverage for reservation cleanup and historical top-level image slimming

## 3. Implementation

- [x] 3.1 Guard serialized upstream `response.create` size before upstream websocket send
- [x] 3.2 Slim historical inline images and oversized tool outputs before failing
- [x] 3.3 Emit deterministic local `payload_too_large` errors instead of relying on upstream `1009`
- [x] 3.4 Persist oversized request dumps for guarded and detected upstream `1009` cases
