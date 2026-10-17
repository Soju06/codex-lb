## MODIFIED Requirements

### Requirement: Source-routed usage uses API-key reservations

The system MUST reserve API-key usage before forwarding an OpenAI-compatible
source-routed request authenticated by an API key, and MUST finalize the
reservation from the upstream OpenAI-compatible `usage` payload when the
request completes.
The finalized input, output, cached-input, and cost values MUST update the same
API-key limit and usage-reporting paths used by subscription-backed requests.
When cancellation interrupts source-routed `/v1/embeddings` upstream
forwarding after reservation creation, the request owner MUST finish releasing
that reservation exactly once despite active cancellation, and MUST then
propagate the original cancellation. Stale-reservation reclamation MUST remain
only a backstop and MUST NOT substitute for request-owned cancellation cleanup.

#### Scenario: Source-routed response finalizes token usage

- **WHEN** an API key calls a source-routed model and the upstream response
  includes `usage.prompt_tokens=100` and `usage.completion_tokens=20`
- **THEN** the API-key reservation is finalized with 100 input tokens and 20
  output tokens
- **AND** `/v1/usage` for that key reflects the completed usage

#### Scenario: Missing usage fails closed for limited keys

- **GIVEN** an API key has a token or cost limit
- **WHEN** a source-routed response succeeds but lacks usable OpenAI `usage`
  fields
- **THEN** the system does not silently finalize zero usage
- **AND** the request fails or is marked failed according to the source-routing
  error contract

#### Scenario: Cancelled source embeddings forwarding releases its reservation

- **GIVEN** a limited API key has created an owned reservation for a
  source-routed `/v1/embeddings` request
- **WHEN** cancellation interrupts the request while upstream embeddings
  forwarding is in flight
- **THEN** the request owner finishes releasing the reservation exactly once
  despite active cancellation
- **AND** the original cancellation propagates after cleanup completes
- **AND** stale-reservation reclamation is not required for that request
