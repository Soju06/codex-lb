## Why

The async continuity change adds a supported `async` field to function and custom tool calls. The fixture sanitizer inherited from main does not recognize that field, so supported captures cannot be rebuilt and the production-field drift gate fails.

## What Changes

Preserve the optional async marker through the existing scalar sanitization rule on function and custom tool calls. Keep identifier remapping, free-text replacement, absent fields and rejection of unknown item fields unchanged.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: preserve supported async markers in sanitized request fixtures.

## Impact

Fixture tooling and regression tests only. No runtime replay, routing, accounting or schema changes.
