## Goals

Image adapter models in dashboard selection.

## Decisions

The Images adapter already accepts models which may be absent from the Responses registry. Reuse its allowlist; preserve dashboard RBAC and source catalog behavior. No new model support or provider pricing is introduced. A key restricted to gpt-image-2 is a concrete example. Empty registries and slug collisions must not hide or duplicate these entries.

## Validation

Exercise public UI/API behavior, edge cases, lint/type checks and strict OpenSpec validation. Capture synthetic before/after screenshots from the upstream baseline and this branch.
