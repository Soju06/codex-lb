## MODIFIED Requirements

### Requirement: Image routes participate in usage accounting and policy

The system SHALL apply API-key allowed-model policy and model-scoped usage
limits to `/v1/images/*` using the publicly-requested `gpt-image-*` value as the
effective model. The system SHALL record the publicly-requested `gpt-image-*`
value (not the internal host model) in the request log's `model` column once the
upstream response id becomes known. A successful image generation or edit that
owns a limited API-key reservation SHALL transfer that reservation exactly once
to persistence-drained settlement using captured `tool_usage.image_gen` tokens,
while the internal Responses stream SHALL NOT receive a second settlement
owner. Failed or cancelled finalization SHALL preserve the completed public
image response and transfer ownership to the tracked retrying release fallback.

#### Scenario: API key allowed-model policy blocks gpt-image-2

- **WHEN** an API key's `allowed_models` list does not include `gpt-image-2`
- **THEN** requests to `/v1/images/generations` or `/v1/images/edits` with `model=gpt-image-2` return 403 `model_not_allowed`

#### Scenario: Request log surfaces the publicly requested image model

- **WHEN** an `/v1/images/*` request completes successfully against an internal host Responses model (for example `gpt-5.5`)
- **THEN** the resulting `request_logs` row has `model` equal to the publicly requested value (for example `gpt-image-2`) so dashboards and usage views surface the user-visible model rather than the internal host model

#### Scenario: Failed image-token settlement retains tracked release ownership

- **GIVEN** a limited API key owns a reservation for a successful image generation or edit request
- **AND** the internal Responses stream receives no API-key reservation
- **AND** the image adapter captures authoritative `tool_usage.image_gen` tokens
- **WHEN** tracked finalization fails or is cancelled while the reservation remains `reserved`
- **THEN** the completed public Images JSON response or SSE completion remains available
- **AND** settlement ownership transfers to a persistence-drained fallback release task
- **AND** transient release failures keep that task tracked and retrying until release succeeds or graceful persistence drain reports timeout
- **AND** a successful fallback restores pre-reserved quota exactly once without recording `response.usage` or starting a second image settlement
