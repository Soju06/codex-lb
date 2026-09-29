Regression reproduced: the real SCIM route consumed all three chunks
instead of stopping at the second, which crossed the body-size limit.

Validation after the fix:
- 65 SCIM unit and integration tests passed on upstream main (f8ffbac20).
- Ruff lint and formatting checks passed.
- Full `ty check` passed.
- Strict change validation passed.

Command: `uv run pytest tests/unit/test_scim*.py tests/integration/test_scim_v2_users.py -q --timeout=120 --tb=short`
