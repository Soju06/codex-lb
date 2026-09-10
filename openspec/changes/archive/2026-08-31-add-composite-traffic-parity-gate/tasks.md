## 1. Contract

- [x] 1.1 Specify fail-closed aggregation across semantic, TLS, and raw HTTP/2
  evidence.
- [x] 1.2 Specify body-size-independent DATA segmentation and informational
  timing treatment.

## 2. Implementation

- [x] 2.1 Compare normalized HTTP/2 DATA frame segmentation as a stable
  dimension.
- [x] 2.2 Add the compact composite gate CLI with input digests, transport
  coverage, timing summaries, and JSON/Markdown output.
- [x] 2.3 Document local/CI use and evidence interpretation.

## 3. Verification

- [x] 3.1 Add focused regression and CLI tests for pass, missing coverage,
  TLS failure, HTTP/2 failure, digest output, and privacy boundaries.
- [x] 3.2 Run formatting, lint, typing, focused/broad tests, strict OpenSpec
  validation, and the gate against retained live evidence.
- [x] 3.3 Archive the verified change into the main specification.
