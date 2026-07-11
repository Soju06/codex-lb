## 1. Contract

- [x] Reconcile local-cap startup wording around the OpenAI SDK contract boundary.
- [x] Add a focused native backend SSE capacity-heartbeat requirement.

## 2. Implementation and coverage

- [x] Emit direct-stream capacity keepalives when HTTP errors are not propagated or the OpenAI SDK contract is disabled.
- [x] Add a native image-bypass regression that proves keepalive-before-release, no premature upstream attempt, and completion after release.
- [x] Run focused and broader proxy tests, Ruff, ty, and strict OpenSpec validation.
