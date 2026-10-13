## ADDED Requirements

### Requirement: Closed conversation archive hours are re-encoded as zstd

The conversation archive writer MUST re-encode a closed hourly `.jsonl.gz` file as `.jsonl.zst` in the background. An hour is closed when the writer targets a later hour and the file has not been modified for at least five minutes. The `.jsonl.gz` original MUST be removed only after the decompressed SHA-256 of the new file equals the decompressed SHA-256 of the original. The writer MUST leave the gzip file in place when it is unreadable, or when verification fails. The swap MUST be skipped if the gzip file changed while it was being re-encoded. An existing `.jsonl.zst` for the same hour MUST never be replaced by less content. The archive reader MUST accept `.jsonl.zst` files wherever it accepts `.jsonl.gz` and legacy `.jsonl` files.

#### Scenario: closed hour is sealed

- **GIVEN** `2026-04-29T10.jsonl.gz` exists and the writer is appending to `2026-04-29T11.jsonl.gz`
- **WHEN** the file has been idle for five minutes
- **THEN** `2026-04-29T10.jsonl.zst` exists with identical decompressed content
- **AND** `2026-04-29T10.jsonl.gz` no longer exists

#### Scenario: verification failure keeps the original

- **WHEN** the re-encoded file does not match the gzip original
- **THEN** the gzip file is kept and the partial output is removed

#### Scenario: reader serves both formats

- **GIVEN** one hour stored as `.jsonl.zst` and another as `.jsonl.gz`
- **WHEN** an authenticated dashboard admin opens request details or lists archive records
- **THEN** records from both files are returned

#### Scenario: append during sealing keeps the original

- **WHEN** the gzip file changes (size or mtime) between the start of re-encoding and the swap, which runs under the archive write lock
- **THEN** the gzip file and its new records are kept, the partial output is removed, and a later pass seals it again

#### Scenario: interrupted format switch

- **GIVEN** both `<hour>.jsonl.gz` and `<hour>.jsonl.zst` exist and the zstd content is a prefix of the gzip content
- **WHEN** the reader lists or searches archives
- **THEN** only the gzip file is read, so no record is returned twice
- **AND** the next sealing pass re-encodes the gzip over the zstd file and removes the gzip after verification

#### Scenario: late appends after sealing

- **GIVEN** a gzip file recreated for an hour that already has a `.jsonl.zst` with different content
- **THEN** the reader reads both files
- **AND** the sealer appends the gzip content to the zstd file as another frame, verifies the combined content, and removes the gzip

#### Scenario: quiet-period file is retried

- **GIVEN** a closed hour skipped because it was modified less than five minutes ago
- **WHEN** the quiet period elapses without further writes
- **THEN** the sealer runs again and seals that hour
