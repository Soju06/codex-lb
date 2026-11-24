## 1. Implementation

- [x] 1.1 Seal closed hourly gzip archive files to zstd with content verification before removal.
- [x] 1.2 Read `.jsonl.zst` files in the archive reader (listing, filename lookup, time-window lookup).

- [x] 1.3 Swap gzip for zstd under the write lock only if the gzip is unchanged since it was read; reconcile a pre-existing `.jsonl.zst` (prefix: replace, otherwise append as a frame).
- [x] 1.4 Reader serves one file per hour when the zstd is a prefix of the gzip.
- [x] 1.5 Retry hours skipped for the quiet period with a timer.

## 2. Verification

- [x] 2.1 Unit tests: rotation, verify-before-delete, corrupt gzip left untouched, existing zst not overwritten, reader on gzip and zstd, append during seal, interrupted switch, late appends, quiet-period retry.
- [x] 2.2 Lint, type check, strict OpenSpec validation.
