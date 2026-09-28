## Why

Issue #1470 requests a standalone recovery hint for an image rollback encountering an unknown migration revision. The current error omits metadata-only stamping and the conditions that make it safe.

## What Changes

- Explain guarded stamping in the schema-ahead error and operator documentation.
- Preserve fail-closed upgrade behavior and all migration execution semantics.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: schema-ahead recovery guidance and its safety preconditions.

## Impact

Only migration error text, documentation and CLI regression coverage. This is partial #1470 work; background data phases and progress reporting remain separate.
