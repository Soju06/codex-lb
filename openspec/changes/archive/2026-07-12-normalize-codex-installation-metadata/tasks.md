# Tasks

## Specification

- [x] Define the selected-account installation metadata consistency contract.

## Implementation

- [x] Add a shared turn-metadata normalization helper.
- [x] Reuse it for Responses payload metadata and transport headers.
- [x] Apply header normalization on HTTP and direct WebSocket egress.

## Verification

- [x] Add focused payload, bridge, HTTP, and WebSocket regression coverage.
- [x] Run focused pytest suites.
- [x] Run Ruff and type checks for touched code.
- [x] Run strict OpenSpec validation.
