# Stabilize Codex requests after upstream forbidden handshakes

## Why

Codex clients can receive a terminal `403 Forbidden` when an upstream edge
rejects the Responses WebSocket handshake with a Cloudflare or equivalent
browser challenge. This is distinct from codex-lb's local firewall and API-key
permission errors. The current transport classifier treats every 403 as a
non-retryable permission failure, so an otherwise usable HTTP Responses path
cannot recover.

Concurrent clients also need bounded recovery: one pre-dispatch edge rejection
must not poison account health, leak an account lease, or cause a retry loop
that prevents unrelated Codex turns from completing.

## What changes

- Detect only explicit edge/browser challenge evidence on a 403 WebSocket
  handshake and allow one same-account HTTP fallback in automatic transport
  mode.
- Keep structured permission errors, `ip_forbidden`, API-key scope failures,
  ordinary Nginx 403 responses, forced WebSocket mode, and hard-pinned turns
  fail-closed with their existing status semantics.
- Apply the same pre-submit safety rule to HTTP bridge startup and ensure
  leases/reservations are settled before any recovery or health bookkeeping.
- Add deterministic unit/integration coverage and an isolated Docker
  concurrency runbook for 16 concurrent workers over three rounds.

## Scope

No database migration, new required setting, firewall relaxation, or host
Codex configuration change is introduced.
