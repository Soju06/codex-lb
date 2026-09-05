"""Gate controlled Codex failure scenarios against a privacy-safe baseline."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    from scripts.traffic_analysis.artifacts import (
        atomic_write_json,
        atomic_write_text,
        file_attestation,
        file_digest,
        read_json,
    )
except ModuleNotFoundError:  # Allow direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.artifacts import (
        atomic_write_json,
        atomic_write_text,
        file_attestation,
        file_digest,
        read_json,
    )

DEFAULT_BASELINE = Path(__file__).with_name("baselines") / "failure-profile-v1.json"
DEFAULT_SCENARIOS = (
    "success",
    "http_429",
    "http_503",
    "http_timeout",
    "sse_incomplete",
    "websocket_reject",
    "websocket_incomplete",
)


def _outcome(turn: Mapping[str, Any], path: str) -> Mapping[str, Any]:
    value = turn.get(path)
    return value if isinstance(value, Mapping) else {}


def project_failure_comparison(value: Mapping[str, Any]) -> dict[str, Any]:
    summary = value.get("summary")
    summary = summary if isinstance(summary, Mapping) else {}
    ab = value.get("failure_path_a_vs_b")
    ab = ab if isinstance(ab, Mapping) else {}
    attempts = ab.get("attempt_counts")
    attempts = attempts if isinstance(attempts, Mapping) else {}
    final = ab.get("final_outcome")
    final = final if isinstance(final, Mapping) else {}
    turns = ab.get("turns")
    turns = turns if isinstance(turns, list) else []
    projected_turns: list[dict[str, Any]] = []
    for turn in turns:
        if not isinstance(turn, Mapping):
            continue
        path_a = _outcome(turn, "path_a")
        path_b = _outcome(turn, "path_b")
        projected_turns.append(
            {
                "path_a_class": path_a.get("class"),
                "path_b_class": path_b.get("class"),
                "path_a_status": path_a.get("http_status"),
                "path_b_status": path_b.get("http_status"),
                "path_a_retry_after": path_a.get("retry_after"),
                "path_b_retry_after": path_b.get("retry_after"),
                "relation": turn.get("relation"),
                "compatible": turn.get("compatible"),
            }
        )
    return {
        "strict_semantic_pass": summary.get("overall_pass") is True,
        "attempt_counts": {
            "path_a": attempts.get("path_a"),
            "path_b": attempts.get("path_b"),
        },
        "ab_all_compatible": ab.get("all_observed_outcomes_compatible"),
        "ab_turns": projected_turns,
        "final_relation": final.get("relation"),
        "final_compatible": final.get("compatible"),
    }


def _diff(expected: Any, actual: Any, prefix: str = "") -> list[str]:
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        differences: list[str] = []
        for key in expected:
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in actual:
                differences.append(path)
            else:
                differences.extend(_diff(expected[key], actual[key], path))
        return differences
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{prefix}.length"]
        differences = []
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual, strict=True)):
            differences.extend(_diff(expected_item, actual_item, f"{prefix}[{index}]"))
        return differences
    return [] if expected == actual else [prefix]


def build_failure_matrix_gate(
    root: str | Path,
    *,
    baseline: str | Path = DEFAULT_BASELINE,
    required_scenarios: Sequence[str] = DEFAULT_SCENARIOS,
) -> dict[str, Any]:
    root_path = Path(root)
    baseline_path = Path(baseline)
    try:
        baseline_value = read_json(baseline_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {
            "passed": False,
            "available": False,
            "error": type(exc).__name__,
            "required_scenarios": list(required_scenarios),
            "scenarios": {},
            "evidence": [],
        }
    baseline_scenarios = baseline_value.get("scenarios") if isinstance(baseline_value, Mapping) else None
    baseline_scenarios = baseline_scenarios if isinstance(baseline_scenarios, Mapping) else {}
    results: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    for scenario in required_scenarios:
        comparison_path = root_path / scenario / "outputs" / "comparison.json"
        try:
            raw = read_json(comparison_path)
            if not isinstance(raw, Mapping):
                raise TypeError("comparison must be a JSON object")
            actual = project_failure_comparison(raw)
            expected = baseline_scenarios.get(scenario)
            if not isinstance(expected, Mapping):
                raise ValueError("scenario is absent from baseline")
            differences = _diff(expected, actual)
            attempts = actual["attempt_counts"]
            nonzero_equal_attempts = (
                isinstance(attempts.get("path_a"), int)
                and attempts["path_a"] > 0
                and attempts.get("path_b") == attempts["path_a"]
            )
            passed = not differences and nonzero_equal_attempts
            attestation = file_attestation(f"failure_{scenario}", comparison_path)
            if attestation["available"] is not True:
                raise OSError("comparison evidence disappeared during attestation")
            evidence.append(attestation)
            results[scenario] = {
                "passed": passed,
                "differences": differences,
                "attempt_counts": attempts,
                "final_relation": actual["final_relation"],
                "strict_semantic_pass": actual["strict_semantic_pass"],
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            results[scenario] = {
                "passed": False,
                "differences": ["missing_or_invalid_evidence"],
                "attempt_counts": None,
                "final_relation": None,
                "strict_semantic_pass": None,
            }
            evidence.append(
                {
                    "label": f"failure_{scenario}",
                    "path": str(comparison_path),
                    "bytes": None,
                    "sha256": None,
                    "available": False,
                    "error": type(exc).__name__,
                }
            )
    return {
        "passed": bool(results) and all(item["passed"] is True for item in results.values()),
        "available": all(item["available"] is True for item in evidence),
        "error": None,
        "baseline": {"path": str(baseline_path), **file_digest(baseline_path)},
        "required_scenarios": list(required_scenarios),
        "scenarios": results,
        "evidence": evidence,
    }


def render_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Controlled failure-matrix gate",
        "",
        f"Overall: **{'PASS' if result.get('passed') is True else 'FAIL'}**",
        "",
        "| Scenario | Attempts A / B | Final relation | Strict B/C | Verdict |",
        "|---|---:|---|---:|---:|",
    ]
    scenarios = result.get("scenarios")
    if isinstance(scenarios, Mapping):
        for name, item in scenarios.items():
            if not isinstance(item, Mapping):
                continue
            attempts = item.get("attempt_counts")
            attempts = attempts if isinstance(attempts, Mapping) else {}
            attempt_text = f"{attempts.get('path_a', '—')} / {attempts.get('path_b', '—')}"
            strict = item.get("strict_semantic_pass")
            strict_text = "PASS" if strict is True else "EXPECTED FAIL" if strict is False else "N/A"
            lines.append(
                f"| `{name}` | {attempt_text} | `{item.get('final_relation') or '—'}` | {strict_text} | "
                f"{'PASS' if item.get('passed') is True else 'FAIL'} |"
            )
            if item.get("differences"):
                lines.append(f"<!-- {name} differences: {json.dumps(item['differences'])} -->")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--require-scenario", action="append", dest="required_scenarios")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_failure_matrix_gate(
        args.root,
        baseline=args.baseline,
        required_scenarios=args.required_scenarios or DEFAULT_SCENARIOS,
    )
    atomic_write_text(args.output, render_markdown(result))
    if args.json_output is not None:
        atomic_write_json(args.json_output, result)
    print(f"Failure matrix gate: {'PASS' if result['passed'] else 'FAIL'}; report: {args.output}")
    return 2 if args.strict and not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
