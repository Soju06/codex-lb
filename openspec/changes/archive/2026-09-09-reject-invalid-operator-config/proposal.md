## Why

Settings accept impossible metrics ports and unknown log-format strings.
Impossible ports can fail in a detached metrics task after startup continues;
misspelled log formats silently select text. Helm constrains metrics ports but
not `config.logFormat`.

## What Changes

- Accept metrics ports only in `1..65535`.
- Accept log format only as `text` or `json`.
- Add matching Helm log-format enum and retain metrics range.
- Preserve defaults, valid boundaries, and collision validation.
- Regenerate settings reference.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: fail-fast runtime and Helm validation.

## Impact

Existing settings declarations, Helm schema, generated reference, and focused
tests. No new setting, dependency, migration, API, frontend, or startup path.
