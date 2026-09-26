## Why

The key-dashboard installer configures authentication but does not install Codex's native model catalog. Custom models and aliases can therefore be absent from clients, lose agent metadata, or attempt an unsupported WebSocket transport.

## What Changes

- Fetch the authenticated native catalog when an exported installer runs, including on repeat runs.
- Install visible models and their complete capability metadata into the shared Codex home and configure `model_catalog_json`.
- Use HTTP when any installed model does not advertise WebSocket preference, so switching to a custom model does not trigger transport retries.
- Back up and protect the catalog alongside configuration and credentials; fail before replacement on download or validation errors.
- Document Python 3 as the Unix installer prerequisite; Windows uses built-in PowerShell.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-key-dashboard`: Refresh key-scoped native model catalogs during client setup, including custom aliases and transport selection.

## Impact

Changes installer rendering and installer regression tests. Adds authenticated `GET /api/key-dashboard/models`, reusing native catalog serialization with mandatory key validation even when proxy auth is optional. No database migration, upstream credential distribution, or frontend layout change.
