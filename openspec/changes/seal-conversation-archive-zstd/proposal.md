## Why

The archive writer appends each batch to the hourly file as its own gzip member, so repeated conversation context across requests is never deduplicated. On a 572 MiB (raw) hourly file gzip -6 gives 301 MiB, while zstd level 9 with a 128 MiB window gives about 34 MiB (24 MiB with a 2 GiB window), with an identical SHA-256 of the decompressed content. A month of traffic at that ratio costs hundreds of gigabytes more than it needs to.

## What changes

- Once an hourly `.jsonl.gz` file is closed (older hour, untouched for 5 minutes), a background thread re-encodes it to `.jsonl.zst` (zstd level 9, 128 MiB window), verifies the decompressed SHA-256 against the expected content, and only then removes the gzip file. The expected content is the gzip content, or the existing `.jsonl.zst` content followed by the gzip content when an existing `.jsonl.zst` for that hour is not a prefix of the gzip (late appends after sealing).
- Corrupt gzip files, a gzip file that changes while it is re-encoded, and verification mismatches leave the gzip file untouched.
- The conversation archive reader serves `.jsonl.zst` next to `.jsonl.gz` and legacy `.jsonl`.

## Scope

`app/core/conversation_archive.py`, `app/modules/conversation_archive/service.py`, and the owning observability requirement. No new setting (the settings surface is ratcheted): the re-encode is lossless and verified, and `zstandard` is already a dependency.
