## Context

`codex-lb` already supports API-key-scoped account selection, request logging, and dashboard/request-log surfaces, but it has no direct endpoint for proactive warmup. Warmup must fan out over an API-key account pool, apply deterministic eligibility rules, and avoid polluting aggregate accounting while still remaining visible in request-log tables.

This change crosses proxy API/service, settings persistence/UI, request-log schema, dashboard aggregates, and API-key usage queries, so a design artifact is needed.

## Goals / Non-Goals

**Goals:**
- Add `POST /v1/warmup` with `default`, `all-or-none`, and `force-update` modes.
- Use API-key account scope semantics for the warmup target pool.
- Persist warmup requests as identifiable request-log rows.
- Exclude warmup rows from aggregate dashboard metrics and API-key usage accounting.
- Add configurable warmup model setting (default `gpt-5.4-mini`) exposed via settings API/UI.

**Non-Goals:**
- Add a generic async job system for warmup.
- Introduce new dashboard pages beyond existing request log and settings surfaces.

## Decisions

### Decision: Add request kind metadata to request logs
- **Choice:** add `request_logs.request_kind` (`normal` or `warmup`) and surface it in request-log API/UI.
- **Why:** we need durable, queryable semantics to both show warmup rows and exclude them from aggregates.
- **Alternatives considered:** infer warmup from model/transport/prompt text (rejected as brittle and operator-config dependent).

### Decision: Exclusion-by-query for aggregate accounting
- **Choice:** update dashboard and API-key aggregate queries to filter out `request_kind='warmup'`.
- **Why:** preserves detailed logs while keeping metrics and key usage stable.
- **Alternatives considered:** avoid logging warmup rows (rejected by explicit visibility requirement).

### Decision: Persist warmup model in dashboard settings
- **Choice:** add `dashboard_settings.warmup_model` with default `gpt-5.4-mini`, wired through settings repository/service/API/UI.
- **Interpretation:** `warmup_model` is an internal settings field; warmup submissions map it to upstream Responses API `model` and do not send `warmup_model` as an upstream field.
- **Why:** follows existing runtime-tunable setting pattern in this codebase.
- **Alternatives considered:** env-only setting (rejected because operators need runtime UI/API update path).

### Decision: Bounded parallel account execution
- **Choice:** execute per-account warmup submissions in parallel with a fixed max concurrency of 5.
- **Why:** reduces warmup completion time for larger account pools while keeping upstream burst pressure bounded and predictable.
- **Alternatives considered:** fully sequential execution (rejected due to slower pool warmup), fully unbounded fan-out (rejected due to upstream pressure risk).

### Decision: Scope-aware target pool with strict all-or-none validation
- **Choice:** scope by assigned accounts when enabled, else all active accounts; `all-or-none` rejects if any account lacks a valid primary 5h 100%-remaining state.
- **Why:** matches product requirements and explicit strict pool semantics.

## Risks / Trade-offs

- **[Risk] Additional schema fields increase migration complexity** -> **Mitigation:** additive nullable/defaulted columns with guarded migration checks.
- **[Risk] Warmup endpoint could generate avoidable upstream load** -> **Mitigation:** tiny deterministic payload, explicit mode semantics, and bounded concurrency cap (5).
- **[Risk] Accounting exclusion misses some query paths** -> **Mitigation:** update all known dashboard/API-key aggregate repositories and add regression tests.
- **[Trade-off] Fixed cap can still be slower for very large pools** -> **Mitigation:** cap is intentionally conservative; tune in a follow-up change if production behavior warrants.

## Migration Plan

1. Add migration for `dashboard_settings.warmup_model` with default/backfill.
2. Add migration for `request_logs.request_kind` with nullable + backfill to `normal` where needed.
3. Deploy backend changes (route/service/repository/schema updates).
4. Deploy frontend settings and request-log badge updates.
5. Validate warmup visibility and aggregate exclusions with integration tests.

Rollback: endpoint can be disabled by rollbacking application code; additive DB columns are backward-compatible and can remain.

## Open Questions

- No blocking open questions for this implementation pass.
