# Add a local LLMBox model source

## Why
Codex already uses this machine's codex-lb account pool. Company inference should be selectable through the same Responses routing, with honest quota visibility, without copying short-lived SSO credentials into dashboard fields.

## What changes
- Add an explicit, immutable `llmbox` source kind, disabled on creation, using the fixed LLMBox HTTPS origin and the server user's existing local login cache.
- Reuse Responses forwarding, cancellation, settlement and request logging; no subscription overflow or model substitution is enabled by this feature.
- Show local credential-cache availability, retained last-24-hour usage, and upstream quota/reset as unknown.
- Add a dashboard preset and compatibility verification. Keep current client routing and running service unchanged until independent validation and coordinated cutover.

## Impact
Model-source API, source selection, forwarding credentials, existing settings UI. No schema migration: kind already exists as a string. No credential synchronization, login flow, or agent-execution proxy.
