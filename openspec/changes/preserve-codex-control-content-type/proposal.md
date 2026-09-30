# Preserve Codex control request media types

## Why

After enabling the v1 search route, a Codex Desktop request reached upstream but
failed with HTTP 400 `Unsupported content type`. The control client preserves a
native lowercase `content-type` and then adds `Content-Type`, producing duplicate
wire headers. Non-JSON control requests also retain an incorrect JSON value.

## What Changes

- Replace the media-type header case-insensitively using the existing helper.
- Preserve the inbound value and native header position without duplicates.
- Cover native search through public routes to the upstream transport boundary,
  plus native/non-native and non-JSON control requests.

## Impact

- Capability: `responses-api-compat`; shared Codex control HTTP client.
- No new configuration, request schema, routing, or persistence changes.
