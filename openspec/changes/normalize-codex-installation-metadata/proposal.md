# Normalize selected-account Codex installation metadata

## Why

Native Codex requests can carry the installation identity in both
`x-codex-installation-id` and the JSON-encoded
`x-codex-turn-metadata.installation_id` field. codex-lb already replaces the
standalone value with the selected account identity, but it can leave the
nested value associated with the client or a previously selected account.
That produces internally inconsistent upstream metadata after account
selection or failover.

## What changes

- Rewrite an existing installation id inside valid turn metadata to the same
  selected-account value used by the standalone installation-id carrier.
- Apply the normalization to Responses payload metadata and transport headers
  on HTTP, HTTP-to-WebSocket, and direct WebSocket egress.
- Preserve missing, malformed, or non-object turn metadata instead of
  inventing or discarding it.

## Impact

- Native Codex outbound metadata only.
- No routing, replay, continuity, database, or credential behavior changes.
