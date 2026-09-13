## 1. Regression Coverage

- [x] 1.1 Add request-level coverage that rejects a one-shot zstd allocation
  attempt before bounded streaming
- [x] 1.2 Capture the regression failing before middleware changes

## 2. Bounded Decompression

- [x] 2.1 Route all zstd decoding through the existing bounded reader
- [x] 2.2 Preserve exact-boundary, overflow, invalid, and stacked encoding
  behavior

## 3. Verification

- [x] 3.1 Run focused unit and integration request-decompression tests
- [x] 3.2 Run static checks and strict scoped OpenSpec validation
- [x] 3.3 Exercise oversized zstd rejection through a live HTTP server and
  verify cleanup
