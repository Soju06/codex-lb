# Tasks: add-codex-images-route-alias

## 1. Specification

- [x] 1.1 Define Codex-base aliases for the existing Images API handlers.
- [x] 1.2 Record the verified upstream Codex wire contract (openai/codex @ rust-v0.144.1) with citations in `context.md`.

## 2. Implementation

- [x] 2.1 Register `/backend-api/codex/images/generations` as an alias of the existing generation handler.
- [x] 2.2 Register `/backend-api/codex/images/edits` as a Codex-native JSON adapter that delegates to the existing edit pipeline.

## 3. Verification

- [x] 3.1 Add route-level regression coverage for both aliases, including Codex JSON data-URL edits.
- [x] 3.2 Run focused image compatibility tests and lint (`35 passed`; Ruff and `git diff --check` clean).
- [x] 3.3 Run strict OpenSpec validation (`openspec validate add-codex-images-route-alias --strict` — valid).
- [x] 3.4 Restart the local LaunchAgent-backed codex-lb service and verify the live aliases return their handlers' 400 validation responses rather than `405`.
- [x] 3.5 Cover trailing-slash behavior: the slashed variants 405 identically on the `/v1` canonical and Codex-base alias surfaces.
