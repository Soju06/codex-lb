## Why
Hosted tool discovery returns tool_search_output items containing loaded tool definitions. Public Responses normalization currently discards these items because the recognized suffixes cover _call and _call_output, not tool_search_output. Clients receive the following function call without the discovery output needed to retain its tool definitions.

## What Changes
- Recognize tool_search_output as a supported public response output item.
- Preserve it in streamed item events, terminal response output, and collected JSON responses.
- Add public-route regression coverage while retaining existing handling of unknown output types.

## Impact
Responses API compatibility only. No new settings, dependencies, database changes, or model-specific behavior.
