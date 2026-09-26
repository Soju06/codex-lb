## Why

Codex sessions using `ch/linxaq` lose their `exec` custom tool at the model-source filter even though the model advertises `code_mode_only`. The remaining `wait` tool cannot start execution, so the model reports that it cannot access the workspace.

## What Changes

- Treat declared code-mode or freeform apply-patch capabilities as support for the `custom` Responses tool type.
- Preserve custom tool declarations and matching choices on both Responses routes, while retaining filtering for undeclared source capabilities.
- Add route-level regression coverage and document the existing explicit metadata workaround for running deployments.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Recognize code-mode and freeform apply-patch declarations when filtering source-bound tools.

## Impact

Changes the model-source capability resolver and Responses route tests. No database migration, setting, UI change, or dependency is needed. Existing deployments can opt the affected models into `custom` through their source metadata before the code fix is deployed.
