from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.traffic_analysis import composite_gate


def _semantic_result(*, websocket_count: int = 1) -> dict[str, Any]:
    counts = {"http_sse": 1, "websocket": websocket_count}
    return {
        "summary": {"overall_pass": True},
        "path_b_vs_c": {"hard_mismatch_count": 0, "hard_mismatches": []},
        "transports": {"path_a": counts, "path_b": counts, "path_c": counts},
    }


def _tls_result(*, failed_transport: str | None = None) -> dict[str, Any]:
    transports: dict[str, Any] = {}
    for transport in composite_gate.DEFAULT_TLS_TRANSPORTS:
        matches = transport != failed_transport
        transports[transport] = {
            "matches": matches,
            "status": "match" if matches else "mismatch",
            "stable_profiles_match": matches,
            "extension_order_matches_direct_variance": matches,
            "baseline_a_reference_vs_a_distance": 0.1,
            "candidate_a_vs_c_distance": 0.1,
            "acceptance_limit": 0.2,
            "cohorts": {label: {"samples": 20} for label in ("path_a_reference", "path_a", "path_c")},
        }
    return {"available": True, "transports": transports}


def _h2_result(*, direct_match: bool = True, routed_match: bool = True) -> dict[str, Any]:
    def comparison(matches: bool) -> dict[str, Any]:
        return {
            "all_observed_match": matches,
            "dimensions": {
                "initial_settings": {"observed": True, "match": matches},
                "request_data_segmentation": {"observed": True, "match": matches},
            },
        }

    return {
        "a_reference_vs_a": comparison(direct_match),
        "a_vs_c": comparison(routed_match),
    }


def _build(
    monkeypatch: pytest.MonkeyPatch,
    evidence: Path,
    *,
    semantic: dict[str, Any] | None = None,
    tls: dict[str, Any] | None = None,
    h2: dict[str, Any] | None = None,
) -> dict[str, Any]:
    monkeypatch.setattr(composite_gate, "compare_paths", lambda **_kwargs: semantic or _semantic_result())
    monkeypatch.setattr(
        composite_gate,
        "analyze_tls_randomization_paths",
        lambda *_args, **_kwargs: tls or _tls_result(),
    )
    monkeypatch.setattr(composite_gate, "load_h2_records", lambda _path: [])
    monkeypatch.setattr(composite_gate, "compare_profiles", lambda *_args: h2 or _h2_result())
    return composite_gate.build_composite_gate(
        semantic_path_a=str(evidence),
        semantic_path_b=str(evidence),
        semantic_path_c=str(evidence),
        tls_path_a_reference=str(evidence),
        tls_path_a=str(evidence),
        tls_path_c=str(evidence),
        h2_path_a_reference=str(evidence),
        h2_path_a=str(evidence),
        h2_path_c=str(evidence),
    )


def test_composite_gate_passes_complete_evidence_without_copying_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "capture.jsonl"
    evidence.write_text('Bearer private-token {"input":"private prompt"}\n')

    result = _build(monkeypatch, evidence)
    rendered = json.dumps(result) + composite_gate.render_markdown(result)

    assert result["overall_pass"] is True
    assert result["strict_exit_code"] == 0
    assert all(item["sha256"] and item["bytes"] for item in result["evidence"])
    assert "private-token" not in rendered
    assert "private prompt" not in rendered
    assert result["timing_informational"]["gate"] is False


@pytest.mark.parametrize(
    ("semantic", "tls", "h2", "failed_section"),
    [
        (_semantic_result(websocket_count=0), _tls_result(), _h2_result(), "semantic"),
        (_semantic_result(), _tls_result(failed_transport="websocket"), _h2_result(), "tls"),
        (_semantic_result(), _tls_result(), _h2_result(direct_match=False), "http2"),
    ],
)
def test_composite_gate_fails_closed_for_partial_or_unstable_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    semantic: dict[str, Any],
    tls: dict[str, Any],
    h2: dict[str, Any],
    failed_section: str,
) -> None:
    evidence = tmp_path / "capture.jsonl"
    evidence.write_text("{}\n")

    result = _build(monkeypatch, evidence, semantic=semantic, tls=tls, h2=h2)

    assert result["overall_pass"] is False
    assert result["strict_exit_code"] == 2
    assert result["sections"][failed_section]["passed"] is False


def test_composite_gate_cli_writes_outputs_and_returns_strict_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "capture.jsonl"
    evidence.write_text("{}\n")
    result = {
        "schema_version": 1,
        "overall_pass": False,
        "strict_exit_code": 2,
        "sections": {},
        "timing_informational": {},
        "evidence": [],
    }
    monkeypatch.setattr(composite_gate, "build_composite_gate", lambda **_kwargs: result)
    report = tmp_path / "gate.md"
    machine = tmp_path / "gate.json"
    path_args = [
        "--semantic-path-b",
        str(evidence),
        "--semantic-path-c",
        str(evidence),
        "--tls-path-a-reference",
        str(evidence),
        "--tls-path-a",
        str(evidence),
        "--tls-path-c",
        str(evidence),
        "--h2-path-a-reference",
        str(evidence),
        "--h2-path-a",
        str(evidence),
        "--h2-path-c",
        str(evidence),
    ]

    exit_code = composite_gate.main([*path_args, "--output", str(report), "--json-output", str(machine), "--strict"])

    assert exit_code == 2
    assert "Overall: **FAIL**" in report.read_text()
    assert json.loads(machine.read_text())["strict_exit_code"] == 2


def test_composite_gate_requires_requested_failure_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "capture.jsonl"
    evidence.write_text("{}\n")

    result = _build(monkeypatch, evidence)
    assert result["sections"]["failure_matrix"]["required"] is False
    assert result["overall_pass"] is True

    monkeypatch.setattr(
        composite_gate,
        "build_failure_matrix_gate",
        lambda *_args, **_kwargs: {
            "passed": False,
            "available": True,
            "scenarios": {"http_429": {"passed": False}},
            "evidence": [],
        },
    )
    failed = composite_gate.build_composite_gate(
        semantic_path_a=str(evidence),
        semantic_path_b=str(evidence),
        semantic_path_c=str(evidence),
        tls_path_a_reference=str(evidence),
        tls_path_a=str(evidence),
        tls_path_c=str(evidence),
        h2_path_a_reference=str(evidence),
        h2_path_a=str(evidence),
        h2_path_c=str(evidence),
        failure_root=str(tmp_path / "failures"),
        require_failure_matrix=True,
    )

    assert failed["overall_pass"] is False
    assert failed["sections"]["failure_matrix"]["required"] is True
