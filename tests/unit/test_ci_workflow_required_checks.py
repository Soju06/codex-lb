import re
from pathlib import Path

CI_WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"


def _ci_workflow_text() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8")


def _job_block(text: str, job_name: str) -> str:
    start_match = re.search(rf"^  {re.escape(job_name)}:\n", text, re.MULTILINE)
    assert start_match is not None
    next_job_match = re.search(r"^  [A-Za-z0-9_-]+:\n", text[start_match.end() :], re.MULTILINE)
    if next_job_match is None:
        return text[start_match.start() :]
    return text[start_match.start() : start_match.end() + next_job_match.start()]


def test_pytest_matrix_required_contexts_are_created_for_non_backend_prs() -> None:
    test_job = _job_block(_ci_workflow_text(), "test")

    assert "name: Tests (pytest, ${{ matrix.slice.name }})" in test_job
    assert "matrix:" in test_job
    assert "\n    if: needs.changes.outputs.backend == 'true'" not in test_job
    assert "name: Skip backend tests for unrelated changes" in test_job
    assert "if: needs.changes.outputs.backend != 'true'" in test_job
    assert "required pytest context satisfied" in test_job


def test_pytest_matrix_real_test_steps_still_run_only_for_backend_changes() -> None:
    test_job = _job_block(_ci_workflow_text(), "test")

    assert "if: needs.changes.outputs.backend == 'true'\n        run: make test-${{ matrix.slice.name }}" in test_job
    for step_name in (
        "Checkout repository",
        "Set up Bun",
        "Cache Bun dependencies",
        "Set up uv",
    ):
        step = test_job.split(f"- name: {step_name}", maxsplit=1)[1]
        assert step.lstrip().startswith("if: needs.changes.outputs.backend == 'true'")


def test_postgres_required_context_is_created_for_non_backend_prs() -> None:
    pg_job = _job_block(_ci_workflow_text(), "test-postgres")

    assert "name: Tests (pytest, PostgreSQL)" in pg_job
    assert "\n    if: needs.changes.outputs.backend == 'true'" not in pg_job
    assert "name: Skip PostgreSQL tests for unrelated changes" in pg_job
    assert "if: needs.changes.outputs.backend != 'true'" in pg_job
    assert "required PostgreSQL context satisfied" in pg_job


def test_postgres_real_test_steps_still_run_only_for_backend_changes() -> None:
    pg_job = _job_block(_ci_workflow_text(), "test-postgres")

    assert "if: needs.changes.outputs.backend == 'true'\n        run: make test-postgres" in pg_job
    for step_name in (
        "Checkout repository",
        "Set up Bun",
        "Cache Bun dependencies",
        "Set up uv",
    ):
        step = pg_job.split(f"- name: {step_name}", maxsplit=1)[1]
        assert step.lstrip().startswith("if: needs.changes.outputs.backend == 'true'")
