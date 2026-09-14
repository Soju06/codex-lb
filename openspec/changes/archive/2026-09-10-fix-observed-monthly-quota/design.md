## Context

See proposal.md. The existing free monthly API test fails for Team on upstream 0f6a31c56ac30804ca1c0fac27ca02c6f59bf2b0. The existing paid-upgrade test requires newer short/weekly samples to supersede stale monthly history.

## Goals / Non-Goals

Keep normalization behind the usage module and preserve the existing API schema. No frontend or routing redesign.

## Decisions

Use the maintainer-approved inclusive 28–32-day duration band in one shared helper. Exact equality excludes the reported 43800-minute payload. Treat only absent or explicit zero-duration secondary windows as placeholders; an unknown duration may describe a real window.

Preserve observed monthly quota without inventing capacity. Replace the plan-only suppression with suppression when a newer 300-minute or 10080-minute primary or secondary sample exists for a plan without monthly capacity. Retain newer monthly samples across stale pre-normalization primary rows.

## Risks / Trade-offs

Historical rows remain until the next ordinary refresh. Timestamp comparison preserves upgrade behavior without deleting history. Existing modules exceed 300 lines; these small changes stay with their existing normalization and mapping responsibilities.

## Migration Plan

No schema changes. Normal polling or live ingestion populates the monthly slot. Reverting the commit restores previous presentation.
