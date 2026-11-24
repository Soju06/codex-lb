## ADDED Requirements

### Requirement: Model-scoped rejection does not alter account health

An upstream error with exact code `model_not_found` MUST be classified as a
model-scoped rejection. Before any downstream-visible output, an otherwise
movable request MAY try the bounded eligible-account failover path because an
account can have a different entitlement. Each attempted account MUST keep its
existing error count, backoff, and availability state after that rejection, so
an unavailable model cannot degrade unrelated valid traffic. An ordinary
`invalid_request_error` whose code is not `model_not_found` MUST retain its
existing non-retryable behavior.

#### Scenario: Unknown model is neutral while a valid neighbour still works

- **GIVEN** two eligible accounts receive a pre-visible `model_not_found`
  rejection for the same requested model
- **WHEN** bounded failover exhausts those accounts
- **THEN** neither account gains an error penalty or backoff
- **AND** a subsequent request for a model either account supports remains
  eligible for normal routing

#### Scenario: Ordinary invalid request remains terminal

- **GIVEN** upstream returns `invalid_request_error` with a code other than
  `model_not_found`
- **WHEN** the request is handled before output
- **THEN** the proxy preserves its existing ordinary invalid-request behavior
