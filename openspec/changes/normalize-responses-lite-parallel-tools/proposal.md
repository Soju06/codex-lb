## Why

The proxy can signal Responses-Lite while forwarding `parallel_tool_calls=true`.
The upstream rejects that combination with `unsupported_value`. Lite requests
need parameter normalization at the same final boundary as their reasoning context.

## What Changes

- Set the boolean `parallel_tool_calls=false` only for established Lite mode.
- Preserve non-Lite settings, payload content, and existing Lite trust rules.
- Test final egress and real route dispatch independently of error-parser fixes.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: Lite-specific parallel-tool normalization.

## Impact

One shared egress finalizer and regression tests. No new flag, migration, image
handling, or error-parser changes. Partial resolution of #2465 Blocker 1.
