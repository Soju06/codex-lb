## ADDED Requirements

### Requirement: Codex-base Images API aliases

The system SHALL expose `POST /backend-api/codex/images/generations` and
`POST /backend-api/codex/images/edits` as Codex-base equivalents of the
existing `/v1/images/generations` and `/v1/images/edits` handlers. The edit
route MUST accept Codex's JSON `images` array, whose entries contain base64
`image_url` data URLs, and decode those entries before it delegates to the
existing edit pipeline. The aliases MUST apply the same authentication,
validation, account-routing, observability, response-shape, and error-envelope
behavior as their `/v1` counterparts. The aliases MUST NOT be included in the
OpenAPI schema because `/v1/images/*` remains the canonical OpenAI-compatible
surface.

#### Scenario: Codex-base image generation uses the existing handler

- **WHEN** a Codex client sends `POST /backend-api/codex/images/generations`
  with an invalid image model
- **THEN** the service returns the same 400 OpenAI `invalid_request_error` with
  `param: model` as `POST /v1/images/generations`

#### Scenario: Codex-base image editing uses the existing handler

- **WHEN** a Codex client sends JSON `POST /backend-api/codex/images/edits`
  without a non-empty `images[].image_url` data URL
- **THEN** the service returns a 400 OpenAI `invalid_request_error` with
  `param: images`, rather than `405 Method Not Allowed` or a missing-prompt error

#### Scenario: Codex-base alias failures before the handler record route observability

- **WHEN** a request to `POST /backend-api/codex/images/generations` or
  `POST /backend-api/codex/images/edits` fails before the route handler runs
  (for example API-key authentication or request-body validation handled by
  the shared exception layer)
- **THEN** the service records the `images_route_complete` observability entry
  (log and metrics) with the same `generations`/`edits` route label as the
  `/v1` counterpart, exactly once
