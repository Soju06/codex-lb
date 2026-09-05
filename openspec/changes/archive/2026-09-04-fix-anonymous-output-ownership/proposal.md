# Preserve ownership of anonymous response output

## Why
Concurrent HTTP requests with identical prompts share a bridge. A response output event without a response ID is incorrectly assigned to a younger request awaiting response.created, leaving the active request empty.

## What Changes
Classify response output events separately from pre-created metadata and errors. Route anonymous output only to the sole started response, including a draining owner. Preserve existing metadata/error matching.

## Impact
HTTP bridge and WebSocket response matching. No database, configuration, or API changes.
