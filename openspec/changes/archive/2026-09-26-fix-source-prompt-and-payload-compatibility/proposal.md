## Why

Fresh route review reproduced four source-pool regressions: prompt references escape credential ownership, prompt-variable files escape the source guard, subscription cleanup misclassifies original compacted history as overrides, and adding a second source rejects valid `top_logprobs` requests.

## What Changes

- Record and resolve prompt template IDs using the existing durable, scoped source ownership records.
- Reject file-bearing prompt variables before direct-source admission, including source overrides.
- Extract original source references from the same forwarding representation used before source overrides.
- Admit valid `top_logprobs` as a neutral direct-source generation control.
- Add route, streaming-client, and shared-database replica regressions with positive controls.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: prompt references and files, original source serialization, and neutral generation controls.

## Impact

Responses source ownership extraction, source request guards, and portability classification. No new settings, schema migration, dashboard changes, transport, or production deployment. Existing subscription input-file routing and source settlement remain authoritative.
