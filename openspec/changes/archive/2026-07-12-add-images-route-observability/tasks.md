## 1. Spec Delta

- [x] 1.1 Add an `images-api-compat` requirement for bounded image-route
  completion observability.
- [x] 1.2 Cover successful and failed image-route telemetry.

## 2. Implementation

- [x] 2.1 Emit bounded `images_route_complete` logs.
- [x] 2.2 Publish image-route Prometheus counters and duration histograms.
- [x] 2.3 Record validation, policy, upstream, image, and streaming outcomes.

## 3. Verification

- [x] 3.1 Add integration coverage for image-route observability.
- [x] 3.2 Run targeted image-route integration tests.
- [x] 3.3 Run `uv run openspec validate add-images-route-observability --strict`.
