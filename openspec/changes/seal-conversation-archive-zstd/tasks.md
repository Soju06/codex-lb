## 1. Implementation

- [x] 1.1 Seal closed hourly gzip archive files to zstd with content verification before removal.
- [x] 1.2 Read `.jsonl.zst` files in the archive reader (listing, filename lookup, time-window lookup).

## 2. Verification

- [x] 2.1 Unit tests: rotation, verify-before-delete, corrupt gzip left untouched, existing zst not overwritten, reader on gzip and zstd.
- [x] 2.2 Lint, type check, strict OpenSpec validation.
