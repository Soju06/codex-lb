## Why

An otherwise successful upstream model refresh can temporarily omit a model
during a staged rollout. The live registry currently replaces the whole model
map, so Codex loses metadata for an explicitly configured model even while
codex-lb can still route requests to it. Codex then falls back to generic
metadata, degrading tool, context, and instruction behavior.

## What Changes

- Retain the last complete live metadata for bundled Codex models omitted by a
  later refresh.
- Return retained-only entries as hidden in the Codex catalog.
- Keep active availability, entitlement indexes, routing, and `/v1/models`
  based only on the current live snapshot.
- Use retained metadata for Responses Lite capability validation.

## Impact

- Affected capability: `model-catalog-compat`.
- Partial upstream catalogs no longer erase metadata for configured Codex
  models.
- Retained metadata cannot advertise or authorize a model that is absent from
  the current live catalog.
- Models outside the bundled Codex catalog are never retained, avoiding stale
  cross-account metadata exposure.
