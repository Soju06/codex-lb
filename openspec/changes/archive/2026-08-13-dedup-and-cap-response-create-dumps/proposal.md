## Why

Issue #1345 reports that `<data-dir>/debug/response-create-dumps` grows without
bound. During the 2026-07-15 `payload_too_large` incident every retry of the
same oversized `response.create` wrote a fresh ~15MB gzipped dump even though
`request_text_sha256` was identical across retries, and dumps had been
accumulating since 2026-05-13. The directory reached 154 files / 1.1GB on a 28G
host (76% full) before manual cleanup.

`_write_response_create_dump` names each dump by wall-clock timestamp only, so
an identical payload always produces a new pair, and nothing ever removes an
old pair. The debug directory is therefore an unbounded write path on the
operator's data volume.

## What Changes

- Skip writing a dump when a dump for the same payload fingerprint already
  exists, keeping the first capture and logging the suppressed duplicate so the
  retry storm stays operator-visible.
- Carry the payload fingerprint in the dump id so duplicate detection is a
  directory lookup rather than a read of every stored meta file.
- Prune the oldest dump pairs on write so the directory retains at most a fixed
  number of pairs, bounding the long-tail accumulation of distinct dumps.
- Add regression coverage for duplicate suppression, distinct-payload
  retention, and pruning.

## Impact

- The debug dump directory becomes bounded on the base install path with no new
  configuration: a retry storm of one payload now costs one pair instead of one
  pair per retry, and distinct dumps are capped at a fixed count.
- Dump ids gain a fingerprint segment. The directory is a debug artifact with
  no consumer contract beyond its location, and the meta file continues to
  carry the full `request_text_sha256`.
- No API, schema, or configuration changes are introduced. The retention bound
  is a hardcoded default, not a new `CODEX_LB_*` setting.
