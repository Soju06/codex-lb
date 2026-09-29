Regression reproduced: the settings API accepted `24:00` with HTTP 200.

Validation after the fix:
- 72 quota-planner unit and integration tests passed on upstream main (f8ffbac20).
- Ruff lint and formatting checks passed.
- Full `ty check` passed.
- Strict change validation and all 67 main specifications passed.

Command: `uv run pytest tests/unit/test_quota_planner.py tests/integration/test_quota_planner_api.py -q --timeout=120 --tb=short`
