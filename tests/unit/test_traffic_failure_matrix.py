from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.traffic_analysis import failure_matrix


def _comparison(expected: dict[str, Any]) -> dict[str, Any]:
    turns = []
    for index, item in enumerate(expected["ab_turns"], 1):
        turns.append(
            {
                "turn": index,
                "path_a": {
                    "class": item["path_a_class"],
                    "http_status": item["path_a_status"],
                    "retry_after": item["path_a_retry_after"],
                },
                "path_b": {
                    "class": item["path_b_class"],
                    "http_status": item["path_b_status"],
                    "retry_after": item["path_b_retry_after"],
                },
                "relation": item["relation"],
                "compatible": item["compatible"],
            }
        )
    return {
        "summary": {"overall_pass": expected["strict_semantic_pass"]},
        "failure_path_a_vs_b": {
            "attempt_counts": expected["attempt_counts"],
            "all_observed_outcomes_compatible": expected["ab_all_compatible"],
            "turns": turns,
            "final_outcome": {
                "relation": expected["final_relation"],
                "compatible": expected["final_compatible"],
            },
        },
    }


def _write_matrix(tmp_path: Path) -> Path:
    baseline = json.loads(failure_matrix.DEFAULT_BASELINE.read_text())
    root = tmp_path / "matrix"
    for scenario, expected in baseline["scenarios"].items():
        output = root / scenario / "outputs" / "comparison.json"
        output.parent.mkdir(parents=True)
        output.write_text(json.dumps(_comparison(expected)))
    return root


def test_failure_matrix_matches_versioned_end_to_end_profiles(tmp_path: Path) -> None:
    root = _write_matrix(tmp_path)

    result = failure_matrix.build_failure_matrix_gate(root)

    assert result["passed"] is True
    assert result["available"] is True
    assert set(result["scenarios"]) == set(failure_matrix.DEFAULT_SCENARIOS)
    assert all(item["sha256"] for item in result["evidence"])
    assert result["scenarios"]["http_timeout"]["strict_semantic_pass"] is False
    assert result["scenarios"]["http_timeout"]["passed"] is True


def test_failure_matrix_fails_missing_scenario_and_attempt_drift(tmp_path: Path) -> None:
    root = _write_matrix(tmp_path)
    missing = root / "http_503" / "outputs" / "comparison.json"
    missing.unlink()

    missing_result = failure_matrix.build_failure_matrix_gate(root)

    assert missing_result["passed"] is False
    assert missing_result["scenarios"]["http_503"]["differences"] == ["missing_or_invalid_evidence"]

    root = _write_matrix(tmp_path / "drift")
    target = root / "websocket_reject" / "outputs" / "comparison.json"
    comparison = json.loads(target.read_text())
    comparison["failure_path_a_vs_b"]["attempt_counts"]["path_b"] = 2
    target.write_text(json.dumps(comparison))

    drift_result = failure_matrix.build_failure_matrix_gate(root)

    assert drift_result["passed"] is False
    assert "attempt_counts.path_b" in drift_result["scenarios"]["websocket_reject"]["differences"]


def test_failure_matrix_cli_writes_compact_outputs(tmp_path: Path) -> None:
    root = _write_matrix(tmp_path)
    report = tmp_path / "report.md"
    machine = tmp_path / "result.json"

    exit_code = failure_matrix.main(
        [
            "--root",
            str(root),
            "--output",
            str(report),
            "--json-output",
            str(machine),
            "--strict",
        ]
    )

    assert exit_code == 0
    assert "Overall: **PASS**" in report.read_text()
    assert json.loads(machine.read_text())["passed"] is True


def test_failure_matrix_fails_if_evidence_disappears_during_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _write_matrix(tmp_path)
    monkeypatch.setattr(
        failure_matrix,
        "file_attestation",
        lambda label, path: {
            "label": label,
            "path": str(path),
            "bytes": None,
            "sha256": None,
            "available": False,
            "error": "FileNotFoundError",
        },
    )

    result = failure_matrix.build_failure_matrix_gate(root)

    assert result["passed"] is False
    assert result["available"] is False
    assert all(item["differences"] == ["missing_or_invalid_evidence"] for item in result["scenarios"].values())
