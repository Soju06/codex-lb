## Why

Tracked files can accumulate at the repository root without review, including one-off agent artifacts that obscure the intended project surface. The existing simplicity-budget mechanism should make additions to that surface explicit and reviewer-visible.

## What Changes

- Define the complete set of allowed tracked repository-root entries in the simplicity-budget configuration.
- Extend the simplicity-budget checker to reject tracked root entries outside that allowlist, while preserving the existing PR-label override behavior.
- Relocate the proxy architecture ADR into its owning OpenSpec capability context and remove obsolete root-level agent debris.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `contribution-simplicity`: Budget the tracked repository-root surface with an explicit allowlist and the existing maintainer override.

## Impact

- `.github/simplicity-budgets.toml` gains the root-entry allowlist.
- `.github/scripts/check_simplicity_budgets.py` checks the committed root tree.
- Proxy architecture context moves under `openspec/specs/proxy-architecture/`; obsolete root files are removed and ignored against recurrence.
