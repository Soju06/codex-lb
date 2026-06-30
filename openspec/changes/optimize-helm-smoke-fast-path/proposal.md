## Why

The kind-based Helm smoke path currently pulls the chart test pod image from Docker Hub even after CI has already built and loaded the application image into kind. That external pull adds avoidable latency and failure risk to the smoke gate.

## What Changes

- Make the Helm test pod image configurable while keeping the default equivalent to the existing BusyBox test pod.
- Override the CI smoke test pod image to the already-built kind-loaded application image.
- Keep external DB smoke coverage focused on external database wiring by running one app replica.
- Add timestamped step logs around major smoke phases.
- Bound `helm test` waits with a configurable timeout so failing test pods do not spend Helm's default timeout window.

## Impact

- Production chart defaults remain equivalent.
- CI smoke logs show phase timing more clearly.
- No `docs/` or changelog updates.
