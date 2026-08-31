## ADDED Requirements

### Requirement: Zstd decoded output is bounded incrementally

The request-decompression middleware MUST consume zstd decoded output
incrementally. Before retaining each decoded chunk, it MUST enforce the
route-specific decompressed-body limit. The middleware MUST NOT decode an
entire zstd body through a one-shot output allocation before applying that
limit.

#### Scenario: Highly compressed zstd body exceeds decoded limit

- **WHEN** a zstd request body expands beyond the route-specific decoded-body
  limit
- **THEN** the middleware stops consuming decoded output once the next bounded
  chunk exceeds the remaining budget
- **AND** the request receives the existing body-too-large response
- **AND** the service remains available for subsequent requests

#### Scenario: Zstd body ends at the decoded limit

- **WHEN** a valid zstd request body expands to exactly the route-specific
  decoded-body limit
- **THEN** the middleware delivers the complete decoded body downstream
