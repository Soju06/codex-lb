## 1. Relocate root documents

- [x] 1.1 Move the ADR-0001 body from the root `DECISIONS.md` into `openspec/specs/proxy-architecture/context.md` with a short relocation header, then delete `DECISIONS.md`.
- [x] 1.2 Delete the root `SUMMARY.md` agent debris and add `DECISIONS.md` beside the existing `SUMMARY.md` line in the `.gitignore` agent-debris block.

## 2. Enforce the root-entry allowlist

- [x] 2.1 Add a `[root_files]` section with a sorted `allowed` list of every tracked repository-root entry to `.github/simplicity-budgets.toml`.
- [x] 2.2 Extend `.github/scripts/check_simplicity_budgets.py` to compare `git ls-tree --name-only HEAD` against the allowlist, reporting each unlisted entry as a violation that names the file and the escape hatch, keeping the override-label and exit-code semantics, and skipping the check when `[root_files]` is absent.

## 3. Verification

- [x] 3.1 Run the budget checker on the final tree (exit 0) and demonstrate that a stray committed root file fails the check with a named violation.
- [x] 3.2 Validate the OpenSpec change and run repository lint.
