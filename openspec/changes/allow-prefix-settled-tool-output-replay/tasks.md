## 1. Prefix-settled tool outputs

- [x] 1.1 Allow output-only suffixes that exactly settle pending direct tool calls from the verified stored prefix.
- [x] 1.2 Reject duplicate, orphan, missing-call-id, and response-owned output items.
- [x] 1.3 Run the raw response-owned `id` guard with the canonical Responses-Lite developer slot recomputed on raw items, failing closed on an unparseable prefix.

## 2. Validation

- [x] 2.1 Add focused replay-safety regression tests.
- [x] 2.2 Validate the OpenSpec delta strictly.
