# Tasks

## Specification

- [x] Define authoritative account capability routing and unknown-state degradation.

## Implementation

- [x] Union same-plan account catalogs for discovery.
- [x] Track per-model and per-tier account capability indexes.
- [x] Require complete active-account catalog coverage before exact filtering.
- [x] Reject explicitly suppressed previously advertised catalog slugs during account selection while preserving unknown mapped-slug fallback.
- [x] Suppress omitted static bootstrap slugs when the first complete account catalog is authoritative.
- [x] Retain a failed account catalog only while its active plan type still matches the plan that produced it.
- [x] Clear capability state when no active accounts remain.
- [x] Apply exact filtering to account selection and HTTP bridge reuse.

## Verification

- [x] Add refresh, registry, selection, and bridge regression coverage.
- [x] Run focused and broad relevant pytest suites.
- [x] Run Ruff, type checks, strict OpenSpec validation, and diff checks.
