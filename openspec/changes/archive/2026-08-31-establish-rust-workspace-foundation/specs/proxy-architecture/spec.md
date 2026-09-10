## ADDED Requirements

### Requirement: Rust migration preserves explicit ownership boundaries

During incremental migration, Python and Rust MUST NOT both own routing policy
or replay decisions for the same operation. Cross-language boundaries MUST
assign selection, persistence, cancellation, retry, and lifecycle ownership,
and executable wiring MUST remain outside reusable libraries.

#### Scenario: Native egress remains a transport slice

- **WHEN** Python submits a native operation
- **THEN** Python retains policy ownership
- **AND** Rust owns only the selected transport attempt
