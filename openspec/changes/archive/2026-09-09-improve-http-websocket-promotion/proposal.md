# Improve HTTP WebSocket promotion

## Why
HTTP agent loops resend full history or use Chat Completions, so metadata-only
smart routing misses them. Native Codex identity also suppresses healthy WS
bridging without evidence of a transport failure.

## What Changes
- Recognize structured multi-turn input and conversation identifiers in smart routing.
- Apply normal policy to native HTTP callers; retain actual WS outage and explicit HTTP guards.
- Use stable, API-key-scoped soft bridge locality for history-only/conversation requests.
- Route subscription Chat Completions through the existing Responses bridge while preserving Chat output and accounting.
- Record bounded admission/bypass reasons and connection lifecycle counters.

## Impact
Responses/Chat routing, bridge locality, transport metrics, compatibility tests.
No new settings, schema, or deployment step. Source-routed Chat stays on its source.
