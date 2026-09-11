## ADDED Requirements

### Requirement: Warmup cancellation preserves measured API-key usage

When a warmup probe has returned exact token usage for an owned API-key reservation, warmup execution SHALL finish reservation finalization with those measured counts before propagating caller cancellation. It MUST NOT replace a completed probe's known usage with a zero-usage failure settlement.

Cancellation before the warmup probe returns measured usage SHALL continue to
fail the owned reservation with zero usage. Deferred cancellation during
finalization of already measured usage MUST propagate before request logging,
warmup-effect recording, or decision completion.

#### Scenario: Cancellation during finalization preserves measured usage

- **GIVEN** a warmup probe returns 7 input, 3 output, and 2 cached input tokens
- **AND** reservation finalization has started with those counts
- **WHEN** caller cancellation arrives before finalization returns
- **THEN** finalization completes exactly once with 7/3/2
- **AND** no failed zero-usage settlement is applied
- **AND** cancellation propagates after finalization
- **AND** no success log or executed decision status is written

#### Scenario: Cancellation before usage remains a zero-usage failure

- **GIVEN** an API-key reservation exists for a warmup
- **WHEN** cancellation arrives before the probe returns usage
- **THEN** the reservation is failed with zero token counts
- **AND** caller cancellation propagates
