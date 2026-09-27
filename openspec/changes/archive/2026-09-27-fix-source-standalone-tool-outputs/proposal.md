## Why

Codex subtask bootstrap can contain a named standalone `function_call_output` without a `call_id`. Direct-source pooling incorrectly treats its client ID as unknown upstream state and returns 409 even though the configured endpoint accepts this self-contained item.

## What Changes

- Recognize strictly validated standalone named function outputs as source-neutral input for direct HTTP Responses routing.
- Preserve the complete forwarding body and existing ownership checks for actual call results and opaque state.
- Cover initial subtasks, retained continuations, pool expansion, replica changes and rejection boundaries through real routes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: source-neutral standalone named tool outputs.

## Impact

Direct-source reference extraction and portability classification, with no schema, configuration, credential, wire payload or subscription replay changes.
