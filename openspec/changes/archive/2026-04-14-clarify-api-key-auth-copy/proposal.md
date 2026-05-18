## Why
The current API key auth copy no longer matches runtime behavior after the proxy auth hardening changes in v1.12.0. The dashboard toggle still says it applies to `/v1/*` only, and the README still says disabling API key auth leaves the proxy open to any client.

In reality, the same guard applies to `/v1/*`, `/backend-api/codex/*`, and `/backend-api/transcribe`, and disabling API key auth now only allows unauthenticated local requests. This mismatch is causing operator confusion, especially for Docker deployments where host-to-container requests are often classified as remote.

## What Changes
- Clarify the dashboard API key auth toggle copy with a short, layout-safe description of protected proxy requests.
- Update the README provider setup and API key auth section to explain that disabled auth only permits local requests.
- Update the main OpenSpec API key auth requirement to match the implemented fail-closed behavior.

## Impact
- Affects dashboard copy for API key auth settings.
- Affects operator-facing README guidance for Codex and OpenAI-compatible clients.
- Aligns the main `api-keys` spec with the current implementation without changing runtime behavior.
