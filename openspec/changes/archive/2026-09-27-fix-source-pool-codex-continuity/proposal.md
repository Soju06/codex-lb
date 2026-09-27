## Why

Enabling four additional credentials for an existing custom model makes Codex requests fail with HTTP 409 before dispatch. A locally captured Codex CLI request reproduces two independent causes: self-contained user messages carry client-generated IDs, and declared web search tools carry `search_content_types`. The current source-pool classifier treats both as upstream-owned state, although the same request works with one source.

## What Changes

- Distinguish IDs on fully self-contained client-authored user/system/developer messages from upstream references when selecting direct Responses sources.
- Accept validated `search_content_types` on declared direct-source web search tools without modifying the forwarded request.
- Retain strict ownership for assistant output IDs, opaque state, incomplete messages, and unknown fields; leave subscription replay policy unchanged.
- Add route regressions for expanding one source to five, continuing across replicas, and retaining conflicting-owner rejection.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: Accept self-contained Codex client message IDs and neutral web-search content controls when pooling direct Responses sources.

## Impact

Direct-source reference extraction and portability classification, route integration coverage, and the model-source-routing specification. No schema or configuration changes. Production source enablement remains unchanged during implementation.
