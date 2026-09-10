## Context

The finalizer already chooses one eligible pending request for its penalty. Subsequent cleanup loops reuse the loop variable before the health call. Passing that later variable would attach another request's scope.

## Goals / Non-Goals

Capture the chosen model and tier alongside the penalty code. Preserve the existing once-per-batch penalty and settlement/logging gates.

## Decisions

Store scalar scope values at selection, before cleanup awaits. Reuse the existing health writer's rejected-scope arguments. No new interface or duplicate persistence path is needed.

## Risks / Trade-offs

Capturing the final loop item would misidentify the rejection in multi-request batches. Verify that a different later request cannot replace the selected scope, and retain all cancellation and release-failure controls.

The handshake decision already owns the request state. Forward its model and tier through the existing connect-error classifier to the same health writer. An end-to-end test imports an account, rejects the WebSocket handshake with 429, and then requires the completed HTTP probe to recover that hold.
