## ADDED Requirements

### Requirement: Closed conversation archive hours are re-encoded as zstd

The conversation archive writer MUST re-encode a closed hourly `.jsonl.gz` file as `.jsonl.zst` in the background. An hour is closed when the writer targets a later hour and the file has not been modified for at least five minutes. The `.jsonl.gz` original MUST be removed only after the decompressed SHA-256 of the new file equals the decompressed SHA-256 of the original. The writer MUST leave the gzip file in place when it is unreadable, when a `.jsonl.zst` for the same hour already exists, or when verification fails. The archive reader MUST accept `.jsonl.zst` files wherever it accepts `.jsonl.gz` and legacy `.jsonl` files.

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
