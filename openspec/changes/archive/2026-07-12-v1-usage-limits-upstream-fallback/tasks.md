## Tasks

- [x] Fall back `limits[]` in `GET /v1/usage` to the visible aggregate upstream quota windows when the authenticated API key has no configured limits, keeping explicit API-key limits preferred.
- [x] Keep `upstream_limits[]` and upstream-quota visibility rules unchanged, so hidden upstream quota never leaks through the fallback.
- [x] Update the `api-keys` spec requirement and scenarios for the legacy `limits[]` fallback.
- [x] Add regression coverage in `tests/integration/test_v1_usage.py` asserting `limits[]` mirrors `upstream_limits[]` for keys without their own limits.
