## Why

A live `input_file.file_id` pin is hard ownership. After thread-scoped
affinity, current Codex locality is the `thread_header` PROMPT_CACHE
row, but `preferred_owner_sticky_inputs` only bypasses
`session_header`. A file-pinned Responses turn therefore rewrites the
thread mapping to the upload account, so later unpinned turns follow
the file owner.

## What Changes

- Treat `thread_header` as the current-Codex soft row that a resolved
  file/response/bridge owner must bypass.
- Keep consulting the raw process-session compatibility row for hard
  conflicts.
- Keep process-session seed insert-if-absent. Do not write or rebind
  the thread row on the required-owner path.
- Keep explicit `turn_state` as hard ownership.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `sticky-session-operations`: A resolved file-pin owner MUST be
  selected without consulting or rewriting the thread-scoped soft
  mapping.

## Impact

- `app/modules/proxy/affinity.py` preferred-owner sticky inputs.
- Focused selection tests.
- No API, schema, setting, dashboard, or wire-format change.
