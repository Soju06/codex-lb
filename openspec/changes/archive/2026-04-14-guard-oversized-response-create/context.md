# Context

## Rationale

The observed production failures were constrained by serialized websocket message size, not by the model token window alone. A thread could still appear to have context headroom while the JSON `response.create` payload exceeded the upstream websocket message budget because historical base64 screenshots and image inputs were being replayed inline on every turn.

## Decisions

- Guard the serialized `response.create` before upstream websocket send instead of waiting for upstream `1009`.
- Preserve the most recent suffix beginning at the final user turn so the current user request and its fresh tool chain are not silently rewritten.
- Slim only historical inline images and oversized historical tool outputs automatically in v1.
- If the request still does not fit after slimming, fail locally with a deterministic `payload_too_large` error.
- Keep oversized payload dumps as an operator diagnostic artifact because size bugs are otherwise hard to root-cause from request logs alone.

## Operational Notes

- The guard leaves headroom below the common 16 MiB websocket message ceiling to avoid last-byte envelope overruns.
- Dump metadata is intended to answer which top-level fields and which `input` items dominate the payload size without requiring console payload tracing.
