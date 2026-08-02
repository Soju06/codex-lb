# Add an OfficeAI reasoning-effort control

## Why

The WPS OfficeAI client can enable model thinking but does not expose an
OpenAI-compatible reasoning-effort selector. Users of a local codex-lb endpoint
therefore cannot choose the reasoning depth without modifying the protected
OfficeAI binaries.

## What Changes

- Add an opt-in, file-backed reasoning-effort override for
  `/v1/chat/completions`, activated by a control file beside the active local
  SQLite database.
- Apply the override only when the request does not already carry an explicit
  reasoning effort.
- Resolve a `maximum` selection to the highest wire-safe effort advertised by
  the selected model.
- Keep API-key enforced reasoning settings authoritative over the local
  OfficeAI override.
- Add a compact Windows control that writes the override file.

## Impact

- **Spec**: `responses-api-compat`
- **Proxy**: optional request normalization on the Chat Completions
  compatibility route.
- **Windows local tooling**: one small always-on-top control bar.
- **Defaults**: unchanged unless the new config-file setting is supplied.
