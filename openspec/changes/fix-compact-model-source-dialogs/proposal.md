## Why

The model source create and edit dialogs render every field of the source form —
name, base URL, upstream key, models, seven capability toggles, context and output
limits, four pricing fields, and the reasoning-effort controls — inside a bare
`DialogContent`. That shell has no height bound and no scroll region, so once the
form is taller than the viewport the dialog stays vertically centred: its title
moves above the visible area and its submit action moves below it, with nothing to
scroll. Operators then cannot save a source at all, because the only submit control
is unreachable.

`fix-compact-api-key-dialog` already solved this for the API key create dialog and
established the viewport-bounded shell used here. The model source dialogs were not
covered by that change.

## What Changes

- Constrain both model source dialog shells to the visible viewport while keeping
  their headers and submit footers outside the scrolling area.
- Move each form body into one internal `min-h-0` vertical scroll region so every
  field stays reachable.
- Add focused browser regression coverage for the viewport-bounded shell, the single
  scroll region, and the persistently visible submit action.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Require the model source create and edit dialogs to keep
  their title, Close control, and submit action inside compact viewports while making
  every field reachable through one internal scroll region.

## Impact

- Affected dashboard components:
  `frontend/src/features/model-sources/components/model-source-create-dialog.tsx` and
  `frontend/src/features/model-sources/components/model-source-edit-dialog.tsx`.
- Affected tests: dashboard browser-smoke coverage.
- No API, schema, persistence, dependency, or configuration changes; form fields,
  validation, submission, and dismissal behavior are unchanged.
