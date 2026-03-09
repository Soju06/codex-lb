## Overview

Keep the existing transport strategy for `/v1/responses` non-streaming requests: call upstream with `stream=true`, consume the SSE stream, and return a single JSON response to the client. Fix the data loss by collecting completed output items during stream consumption and merging them into the terminal response payload when the terminal payload does not already contain output items.

## Decisions

### Collect output items from SSE

Track `response.output_item.added` and `response.output_item.done` events by `output_index`. `done` events overwrite earlier `added` snapshots for the same index.

### Prefer terminal output when present

If the terminal `response.completed.response` or `response.incomplete.response` already includes a non-empty `output` array, return it unchanged. Otherwise, synthesize `output` from the collected SSE item events in `output_index` order.

### Preserve raw payloads

Do not validate or reshape collected output items beyond the top-level response parsing already used by `OpenAIResponsePayload`. This preserves reasoning-specific and vendor-specific fields that clients may rely on.
