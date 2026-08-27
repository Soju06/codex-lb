# Tasks

- [x] Add the narrow WebSocket edge-challenge classifier and safe error metadata.
- [x] Integrate one-shot HTTP fallback for automatic Responses streaming and
      pre-submit HTTP bridge startup.
- [x] Preserve forced-WebSocket, permission, firewall, hard-pin, and
      reservation-settlement behavior.
- [x] Treat downstream disconnects during capacity/recovery waits as normal
      cancellation and keep cleanup/health bookkeeping deterministic.
- [x] Add unit and integration regressions for challenge vs ordinary 403 paths,
      replay safety, and concurrent resource cleanup.
- [x] Build an isolated Docker image and run the 16×3 Codex CLI concurrency
      test; record client-visible 403s and transport outcomes.
- [x] Run OpenSpec validation, focused pytest, ruff, and final host-config
      integrity checks.
