---
name: "codex-review-loop"
description: |
  Adversarial PR code review using Codex CLI.
  Codex review (~20-50 min) -> structured findings -> HITL approval -> fix loop.
metadata:
  author: codex-lb
  version: "2.0.0"
  argument-hint: "[--pr <N>] [--base <branch>] [--uncommitted]"
---

# Codex Review Loop

Adversarial code review via Codex CLI, structured finding analysis,
HITL-gated fix loop, optional re-verification.

## Arguments

- `--pr <N>` — review PR number N (default: auto-detect most recent open PR)
- `--base <branch>` — review changes against base branch
- `--uncommitted` — review uncommitted local changes

## Phases

1. **Scope Resolution** — determine review target (PR/branch/uncommitted)
2. **Codex Review** — launch adversarial review via Codex CLI (~20-50 min)
3. **Finding Analysis** — parse raw output into structured findings
4. **Atomic Fix Loop** — fix, verify, commit each approved finding
5. **Final Report** — summary of all findings, fixes, and verification status

---

## Phase 1: Scope Resolution

Determine the review target and extract the base branch.

- **PR mode** (default): Use `gh pr view` / `gh pr list` to resolve base branch
  and display PR metadata (title, file count, additions/deletions).
- **Branch mode** (`--base`): Use specified base branch directly.
- **Uncommitted mode** (`--uncommitted`): Review working tree changes.

If no argument is given, auto-detect the user's most recent open PR and confirm.

---

## Phase 2: Codex Review

Launch the adversarial review as a background process.

1. Run the review script with `run_in_background=true`:
   ```
   bash <skill-dir>/scripts/codex-subagent.sh --base <branch>
   ```
   Note: Codex CLI v0.105.0+ does not support combining `--base`/`--commit`
   with a custom prompt. The built-in review logic is used automatically.
   For `--uncommitted` mode (no diff target), pipe stdin for custom instructions:
   ```
   cat <prompt-file> | bash <skill-dir>/scripts/codex-subagent.sh --uncommitted
   ```
2. Inform the user the review is running (~20-50 min).
3. The script parses Codex output and returns the final review text.

### Error handling

| Exit code | Meaning | Action |
|-----------|---------|--------|
| 0 | Success | Proceed to Phase 3 |
| 1 | Codex error | Show error, offer retry or abort |
| 127 | codex not found | Guide: `npm i -g @openai/codex` |

### Environment overrides

| Variable | Purpose |
|----------|---------|
| `CODEX_REVIEW_MODEL` | Override Codex model |
| `CODEX_REVIEW_REASONING` | Override reasoning effort |

---

## Phase 3: Finding Analysis

Parse the raw Codex output into structured findings.

### References

- **Schema**: `references/schemas/review-findings.md` — field definitions,
  severity/category/effort enums, status lifecycle
- **Convention rules**: `.agents/skills/project-conventions/conventions.md` —
  project coding conventions to cross-reference

### Severity escalation/downgrade guide

- **Escalate to Critical**: Unvalidated external input, secret exposure,
  injection vectors, data integrity compromise
- **Escalate to High**: API contract change without test update, possible
  NoneType error, blocking I/O in async context
- **Downgrade to Medium**: Correct functionality but violates conventions,
  duplicate logic, unnecessary complexity
- **Downgrade to Low**: Pure style, unused import, missing docstring

### Convention-to-category mapping

- Typing violations (dict, Mapping, object, getattr) -> `typing`
- Anti-patterns (speculative fallbacks, duplicate state) -> `convention`
- Structure violations (core/modules boundary, DI bypass) -> `architecture`
- Testing gaps (contract change without test update) -> `testing`

### Process

1. Parse each finding from the raw output
2. Assign severity and category using the schema definitions and guide above
3. Cross-reference with project conventions
4. Estimate fix effort
5. Deduplicate (keep the most severe instance)
6. Sort: severity desc, file path asc

---

## HITL Gate 1: Finding Review

Present all findings to the user as a summary table
(ID, severity, category, file:line, title, effort).

Ask the user to choose a fix scope:
- Fix all (recommended)
- Fix Critical/High only
- Report only (no fixes)

---

## Phase 4: Atomic Fix Loop

For each approved finding, execute an atomic fix-verify-commit cycle.

### Per finding:

1. **Fix**: Read target file(s), apply the fix.
2. **HITL Gate 2** (conditional): For Critical/High findings that modify existing
   logic (not just adding new code), show the current and proposed code to the
   user for confirmation. Medium/Low findings are auto-fixed.
3. **Verify** (all must pass):
   - `uvx ruff check .`
   - `uvx ruff format --check .` (auto-fix and re-check if needed)
   - `uv run ty check`
   - `uv run pytest` (mapped test files, or full suite if no mapping found)
4. **Commit**: `fix(review): P{i} - {title}`
5. **On failure**: Roll back changes, mark finding as `skipped`, continue to next.

---

## HITL Gate 3: Re-verification

After all fixes, present the commit stack and skipped items.

Options:
- Re-verify via Codex (`--uncommitted` mode, max 2 iterations)
- Review commits
- Done — output final report

---

## Final Report

Output a summary including:
- PR metadata (number, title, base/head)
- Loop iteration count
- Findings table (ID, severity, category, title, status, commit hash)
- Commit stack
- Verification status (ruff, ty, pytest)
