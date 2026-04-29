## 1. Routing Policy

- [x] 1.1 Add a non-blocking backend Codex session-header capability hint.
- [x] 1.2 Add a sticky ChatGPT owner selectable check that ignores usage-drain thresholds.
- [x] 1.3 Suppress usage-drain Platform fallback for hinted backend Codex hard-affinity requests while the sticky owner is selectable.
- [x] 1.4 Preserve the selectable sticky ChatGPT owner for backend Codex `x-codex-turn-state` streaming selection instead of reallocating solely on sticky budget pressure.
- [x] 1.5 Propagate the `x-codex-turn-state` sticky budget guard to owner-forwarded HTTP bridge requests.

## 2. Verification

- [x] 2.1 Add unit coverage for provider selection with backend Codex continuity hints.
- [x] 2.2 Add integration coverage for backend Codex responses with an existing sticky ChatGPT owner.
- [x] 2.3 Add regression coverage for compact requests and force fallback with an existing sticky ChatGPT owner.
- [x] 2.4 Add regression coverage for owner-forwarded HTTP bridge `x-codex-turn-state` requests.
- [x] 2.5 Run targeted tests and OpenSpec validation.
