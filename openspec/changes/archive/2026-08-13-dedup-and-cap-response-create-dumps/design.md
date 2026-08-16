## Context

`_write_response_create_dump` in `app/modules/proxy/_service/response_create.py`
is reached from two callers: the pre-send size guard
(`_response_create_text_with_size_guard`) and the upstream oversized-close
handler (`_maybe_dump_oversized_response_create_request`). Both ignore the
return value, so the return is free to express "did not write".

The function already computes `sha256(request_text)` and stores it in the meta
file as `request_text_sha256`, but the dump id is built only from a wall-clock
timestamp plus transport/model/request slugs. Two retries of one payload
therefore differ in dump id while being byte-identical, which is exactly the
incident in #1345.

## Decisions

1. **Fingerprint in the dump id.** Append a 16-hex-character prefix of the
   existing `request_text_sha256` to the dump id. Duplicate detection becomes a
   single `glob` on the directory instead of opening and JSON-parsing every
   stored meta file on a path that is already degraded.
2. **First capture wins.** Suppress the later duplicate rather than replacing
   the earlier one. The first capture is the one closest to the onset of the
   incident, and keeping it makes the write path idempotent per payload.
3. **Suppression stays visible.** Log the skipped duplicate at `warning` with
   the fingerprint and the existing dump path. The operator signal in #1345 was
   the repeated dump line; dropping the write silently would remove it. Only
   the bytes are suppressed, not the evidence.
4. **Prune on write, by count.** Enforce the cap after a successful write, as
   the issue suggests. Dump ids are timestamp-prefixed with microseconds, so
   lexicographic order is chronological and pruning needs no `stat` calls.
   Deleting a dump also deletes its meta sibling so pairs never desynchronize.
5. **Hardcoded bound, not a setting.** `_RESPONSE_CREATE_DUMP_MAX_PAIRS = 20`
   is a module constant reachable through the existing `_service_global_or`
   indirection, matching `_OVERSIZED_RESPONSE_CREATE_LARGEST_ITEMS`. Per
   simplicity rules P1/P2 this needs no `CODEX_LB_*` setting: it is an
   internals-only debug bound and the base install path stays zero-config.
6. **Count only, not bytes or age.** Deduplication removes the amplifier that
   produced 154 near-identical files; the count cap bounds the residual tail of
   distinct payloads. A byte or age cap would add a second budget and a second
   constant for no additional protection against the reported failure.

## Risks and Mitigations

- **Losing a distinct dump to the cap.** Only the oldest pairs beyond 20 are
  removed, and dumps are a debug aid rather than a durable record. Before this
  change the practical outcome was a full disk, which loses every dump plus the
  database.
- **A recurring payload never re-captured.** Intentional: the retained pair is
  byte-identical, and the suppression log still records each recurrence with
  its request id.
- **Over-eager dedup suppressing unrelated payloads.** A 16-hex prefix (64
  bits) over a directory bounded at 20 entries makes collision negligible, and
  `test_response_create_dump_keeps_distinct_payloads` pins that distinct
  payloads still produce distinct pairs.
- **Filesystem races between replicas.** Writes are best-effort and already
  wrapped; `unlink` uses `missing_ok=True` and both helpers swallow `OSError`,
  so a concurrent prune cannot fail a request.

## Verification

- Unit coverage for duplicate suppression, distinct-payload retention, and
  prune-on-write in `tests/unit/test_proxy_utils.py`.
- Fail-on-main check: the duplicate-suppression test fails against `main`,
  writing two pairs for one payload.
- `ruff check` / `ruff format --check` / `ty check` on the changed modules and
  the full `tests/unit/test_proxy_utils.py` suite.
