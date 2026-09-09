## Why

Strict canonical OpenSpec validation currently fails 22 capabilities only because their `Purpose` sections still contain historical `TBD`/placeholder text. This documentation debt obscures the intent of otherwise valid requirements and blocks the archive gate without representing a product behavior defect.

## What Changes

- Replace each of the 22 placeholder Purpose sections with concise, capability-specific purpose statements.
- Keep all requirements, scenarios, schemas, APIs, and runtime behavior unchanged.
- Validate the canonical spec set in normal and strict modes and record the before/after result.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. This is a documentation-only cleanup; no normative requirement changes are introduced.

## Impact

Only `openspec/specs/**/spec.md` Purpose prose and this change's documentation artifacts are affected. No application code, tests, user-facing `docs/`, configuration, database, or deployment state changes.
