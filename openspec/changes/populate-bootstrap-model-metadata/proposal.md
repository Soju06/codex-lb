## Why

Before the first upstream model-registry refresh completes, codex-lb still needs
to serve usable model catalogs. Returning an empty catalog or under-specified
placeholder entries makes freshly started instances look model-less to Codex and
OpenAI-compatible clients until a refresh succeeds.

## What Changes

- Seed the uninitialized model registry with a conservative static catalog of
  known upstream model slugs.
- Include representative upstream metadata for those bootstrap entries so
  `/backend-api/codex/models` remains useful before refresh.
- Use bootstrap metadata when resolving websocket preference before refresh.
- Keep refreshed upstream registry data authoritative once it exists.

## Impact

Freshly started instances can answer `/v1/models` and
`/backend-api/codex/models` immediately with known Codex model entries. Operators
still get live upstream metadata after the first successful model refresh.
