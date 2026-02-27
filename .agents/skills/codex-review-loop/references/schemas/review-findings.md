# Review Findings Schema

Field definitions for structured findings. The Finding Analyzer converts Codex raw output into this schema.

## Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `int` | Yes | Sequential number (P1, P2, ...) |
| `severity` | `enum` | Yes | `Critical` \| `High` \| `Medium` \| `Low` |
| `category` | `enum` | Yes | `security` \| `bug` \| `typing` \| `architecture` \| `testing` \| `performance` \| `compatibility` \| `convention` |
| `file` | `string` | Yes | File path (relative to project root) |
| `lines` | `string` | Yes | Line range (e.g., `142`, `30-45`) |
| `title` | `string` | Yes | One-line summary, 80 characters or less |
| `description` | `string` | Yes | Detailed description — what is wrong and why it matters |
| `suggestion` | `string` | Yes | Specific fix suggestion (including code snippet) |
| `impact` | `string` | Yes | Impact if left unfixed |
| `effort` | `enum` | Yes | `trivial` \| `small` \| `medium` \| `large` |
| `status` | `enum` | Yes | `pending` \| `approved` \| `fixed` \| `skipped` \| `wont-fix` |

## Severity Definitions

| Severity | Definition | HITL |
|----------|-----------|------|
| **Critical** | Security vulnerability, data loss, production crash | User confirmation required |
| **High** | Bug, contract violation, expected CI failure | User confirmation required |
| **Medium** | Code quality, convention violation, maintainability | Auto-fix |
| **Low** | Style, optimization, documentation | Auto-fix |

## Category Definitions

| Category | Description |
|----------|-------------|
| `security` | Security vulnerability (auth, injection, secret exposure, CORS) |
| `bug` | Functional bug (logic error, wrong condition, missing error handling) |
| `typing` | Type safety (dict→Pydantic, missing types, schema mismatch) |
| `architecture` | Architecture violation (layer violation, DI bypass, god-class) |
| `testing` | Test deficiency (contract change without test update, broken assertion) |
| `performance` | Performance issue (N+1 query, blocking I/O, unnecessary iteration) |
| `compatibility` | Compatibility issue (OpenAI-compatible endpoint breaking change) |
| `convention` | Project convention violation (CLAUDE.md rule violation) |

## Effort Definitions

| Effort | Description |
|--------|-------------|
| `trivial` | 1-2 line change (add import, add type hint) |
| `small` | 3-10 line change (modify function signature, simple refactor) |
| `medium` | 10-50 line change (add new class/function, logic change) |
| `large` | 50+ lines (architecture refactor, multi-file modification) |

## Status Lifecycle

```
pending → approved → fixed
                   → skipped (verification failed)
        → wont-fix (user rejected)
```

- `pending`: Initial state when created in Phase 3
- `approved`: User included in fix scope at HITL Gate 1
- `fixed`: Fix applied + verified + committed in Phase 4
- `skipped`: Rolled back due to verification failure in Phase 4
- `wont-fix`: Explicitly excluded by user at HITL Gate

## Example

```markdown
| # | Sev | Category | File:Line | Title | Effort | Status |
|---|-----|----------|-----------|-------|--------|--------|
| P1 | Critical | security | app/modules/proxy/service.py:142 | Missing auth check on proxy endpoint | medium | pending |
| P2 | High | typing | app/modules/proxy/schemas.py:30 | dict used instead of ProxyRequestSchema | small | pending |
| P3 | Medium | convention | app/modules/usage/service.py:88 | Epoch timestamp instead of ISO 8601 | trivial | pending |
| P4 | Low | convention | app/core/config.py:15 | Unused import os.path | trivial | pending |
```
