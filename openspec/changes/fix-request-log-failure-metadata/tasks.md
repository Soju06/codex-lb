- [x] Add local proxy routing failure codes to `_LOCAL_PROXY_ERROR_CODES` in
  `app/modules/proxy/service.py`.
- [x] Update migration `20260526_000000_add_request_log_failure_metadata.py`
  to descend from the current `main` merge head.
- [x] Add missing migration revision
  `20260601_000000_merge_relative_availability_and_usage_raw_heads.py` when needed by this branch lineage.
- [x] Add unit coverage that the three local routing codes produce `upstream_status_code = null`
  in `_request_log_failure_metadata()`.
- [x] Add OpenSpec change entries describing:
  - local routing failure metadata behavior
  - migration linearization for new request-log columns
- [x] Run `uv run ruff check`, `uv run ty check`, and migration checks relevant to this change.

