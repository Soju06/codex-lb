## Why
The dashboard account-add OAuth flow currently stores browser PKCE state in a single global slot. When two operators or tabs start browser OAuth concurrently, the second start overwrites the first flow's `state` and `code_verifier`, so the first callback fails with `Invalid OAuth callback: state mismatch or missing code.`

## What Changes
- Isolate dashboard OAuth flows so concurrent browser sign-ins do not overwrite each other.
- Add a stable flow identifier that the dashboard uses for status and device completion requests.
- Match browser callbacks against the correct in-flight flow using the callback `state` token.
- Add regression coverage for overlapping browser OAuth flows.

## Impact
- Affects the dashboard account-add OAuth flow in backend and frontend.
- Adds backward-compatible OAuth flow identifiers to dashboard OAuth request/response payloads.
- Preserves the existing browser and device OAuth UX while allowing concurrent sessions.
