# Tasks

## Step 1: OpenSpec

- [x] Add proposal, design, and the `responses-api-compat` delta; run `openspec validate`.

## Step 2: Backend

- [x] Remove `Settings.upstream_stream_transport`; add `CODEX_LB_UPSTREAM_STREAM_TRANSPORT` to `_REMOVED_SETTINGS`; lower the settings-surface ratchet; regenerate `docs/reference/settings.md`.
- [x] Add `configured_upstream_stream_transport()` and use it in stream retry, the websocket 426 gate, and the HTTP bridge bypass.
- [x] Drop the env read from the core proxy client (constant `auto` base, override from the service).
- [x] Restrict the API schema to `auto|http|websocket`; seed and ORM default `auto`.
- [x] Add the data migration (`default` -> `auto`, server default) with a unit test.

## Step 3: Frontend, Helm, docs

- [x] Remove the "Server default" option and the `default` literal from schemas, factories, handlers, and i18n (en, ko, zh-CN).
- [x] Remove the variable from Helm `configmap.yaml` / `values.yaml`.
- [x] Point `docs/client-setup.md` and `docs/traffic-parity.md` at the dashboard setting.

## Step 4: Verification

- [x] `make lint`, targeted `uv run pytest`, frontend lint/typecheck/test, `openspec validate`.
