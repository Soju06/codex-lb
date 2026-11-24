## Context

See proposal.md for the five review gaps. Historical PR revisions reused identifiers after their ancestors changed; an identifier alone cannot establish the schema already installed.

## Goals / Non-Goals

Repair observed behavior with existing normalization and lifecycle mechanisms. Preserve the maintainer-directed linear graph, credential retirement semantics, policy values, and unrelated upstream data. Avoid a generic migration reconciliation framework or new runtime settings.

## Decisions

- Recognize historical migration states from schema and ledger together, then apply only missing migrations. Blind parent restoration or blind restamping can replay applied revisions and is excluded.
- Keep rejection cleanup owned by existing registered finalization tasks through cancellation and settlement exceptions; preserve client error fields and reader-owned requests.
- Use the evaluator's window precedence for account summaries. Derive provider remaining from usable plus reserved credits when applying the secondary constraint, so weekly reserves remain distinguishable from consumption.

## Risks / Trade-offs

- Historical credential transitions can be destructive if replayed: verify representative historical schemas and saved data, including retired credentials, and fail clearly on unrecognized combinations.
- Cancellation can arrive at any awaited release: test partial failures through real proxy entry points.
- Quota displays combine different capacities: test absolute credits and rendered output, including real provider exhaustion.

## Migration Plan

Exercise supported historical schemas and canonical schemas through normal upgrade tooling on disposable databases. Preserve the revision graph and ordinary upstream upgrades. Capture dashboard evidence with identical fixtures before and after. Publish only after focused checks and independent Opus review; do not merge or deploy.
