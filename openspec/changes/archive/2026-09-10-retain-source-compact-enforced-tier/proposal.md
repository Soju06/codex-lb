## Why

PR #2324 applies subscription tier fallback before selecting a compact source. An assigned source sharing a known native model loses its enforced tier in request accounting.

## What Changes

- Run subscription tier fallback only after the compact source branch.
- Verify both compact endpoints through the public request-log API.
- Repair two existing budget-failure test doubles for the new optional payload argument.

## Impact

Source compact accounting retains the enforced tier. Native fallback and configured HTTP transport stay unchanged. Closes #2318 remains the owning PR issue.
