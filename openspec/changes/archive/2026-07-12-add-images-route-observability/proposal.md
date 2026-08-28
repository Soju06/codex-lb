# Add image route observability

## Why

Operators need bounded route-completion telemetry for `/v1/images/generations`
and `/v1/images/edits` so image-route failures, policy rejections, and latency
are visible without logging prompts, uploaded images, or upstream payloads.

## What Changes

- Emit `images_route_complete` logs for image generation and edit requests.
- Record Prometheus counters and duration histograms with bounded labels:
  route, public image model, stream flag, HTTP status, and outcome.
- Classify validation, model-policy, upstream, image-generation, and streaming
  outcomes without including request content or binary data in labels/logs.

## Impact

- **Spec**: `images-api-compat`
- **Behavior**: image routes gain completion logs and metrics for success and
  failure paths.
