## ADDED Requirements

### Requirement: Image routes expose bounded operational observability

The system SHALL emit structured route-completion logs and Prometheus metrics
for `/v1/images/generations` and `/v1/images/edits`. Observability labels MUST
be bounded to route, effective public model, stream flag, HTTP status, and
outcome, and MUST NOT include prompts, image bytes, file names, access tokens,
or raw upstream payloads.

#### Scenario: Successful image request records completion telemetry

- **WHEN** an `/v1/images/generations` or `/v1/images/edits` request completes
  successfully
- **THEN** the service emits an `images_route_complete` log line with the public
  image route, public model, stream flag, status, outcome, and duration
- **AND** increments `codex_lb_image_requests_total` and observes
  `codex_lb_image_request_duration_seconds` with the same bounded labels

#### Scenario: Failed image request records completion telemetry

- **WHEN** an image request is rejected by validation or mapped from an
  upstream/image-generation error
- **THEN** the service emits the same bounded `images_route_complete` fields
  with a non-success outcome
- **AND** increments the image request counter and duration histogram without
  logging prompt or binary image content
