"""Fail-closed aggregate gate for Codex traffic parity evidence.

This command does not capture traffic. It combines existing metadata-only
semantic, TLS-cohort, and raw HTTP/2 evidence into one compact verdict.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    from scripts.traffic_analysis.artifacts import atomic_write_json, atomic_write_text, file_attestation
    from scripts.traffic_analysis.compare import compare_paths
    from scripts.traffic_analysis.failure_matrix import (
        DEFAULT_BASELINE as DEFAULT_FAILURE_BASELINE,
    )
    from scripts.traffic_analysis.failure_matrix import DEFAULT_SCENARIOS as DEFAULT_FAILURE_SCENARIOS
    from scripts.traffic_analysis.failure_matrix import build_failure_matrix_gate
    from scripts.traffic_analysis.http2_profile import compare_profiles
    from scripts.traffic_analysis.http2_profile import load_records as load_h2_records
    from scripts.traffic_analysis.tls_randomization import TLS_TRANSPORTS, analyze_tls_randomization_paths
    from scripts.traffic_analysis.turns import load_capture
except ModuleNotFoundError:  # Allow direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.artifacts import atomic_write_json, atomic_write_text, file_attestation
    from scripts.traffic_analysis.compare import compare_paths
    from scripts.traffic_analysis.failure_matrix import (
        DEFAULT_BASELINE as DEFAULT_FAILURE_BASELINE,
    )
    from scripts.traffic_analysis.failure_matrix import DEFAULT_SCENARIOS as DEFAULT_FAILURE_SCENARIOS
    from scripts.traffic_analysis.failure_matrix import build_failure_matrix_gate
    from scripts.traffic_analysis.http2_profile import compare_profiles
    from scripts.traffic_analysis.http2_profile import load_records as load_h2_records
    from scripts.traffic_analysis.tls_randomization import TLS_TRANSPORTS, analyze_tls_randomization_paths
    from scripts.traffic_analysis.turns import load_capture


DEFAULT_SEMANTIC_TRANSPORTS = ("http_sse", "websocket")
DEFAULT_TLS_TRANSPORTS = TLS_TRANSPORTS


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def _distribution(values: Sequence[float]) -> dict[str, Any] | None:
    finite = sorted(value for value in values if math.isfinite(value) and value >= 0)
    if not finite:
        return None
    return {
        "samples": len(finite),
        "min_ms": round(finite[0], 3),
        "p50_ms": round(_percentile(finite, 0.5), 3),
        "p95_ms": round(_percentile(finite, 0.95), 3),
        "max_ms": round(finite[-1], 3),
    }


def _timing_summary(path: str | Path) -> dict[str, Any]:
    try:
        records = load_capture(path, strict=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"available": False, "error": type(exc).__name__, "transports": {}}

    values: defaultdict[str, list[float]] = defaultdict(list)
    websocket_flows: defaultdict[str, list[float]] = defaultdict(list)
    for record in records:
        if not isinstance(record, Mapping):
            continue
        kind = record.get("kind")
        transport = record.get("transport")
        duration = record.get("duration_ms")
        if kind == "http" and isinstance(transport, str) and isinstance(duration, (int, float)):
            values[transport].append(float(duration))
        if kind == "websocket_message":
            flow_id = record.get("flow_id")
            timestamp = record.get("timestamp")
            if isinstance(flow_id, str) and isinstance(timestamp, (int, float)):
                websocket_flows[flow_id].append(float(timestamp))
    for timestamps in websocket_flows.values():
        if len(timestamps) >= 2:
            values["websocket"].append((max(timestamps) - min(timestamps)) * 1000.0)
    return {
        "available": True,
        "error": None,
        "transports": {
            transport: distribution
            for transport, samples in sorted(values.items())
            if (distribution := _distribution(samples)) is not None
        },
    }


def _semantic_section(
    path_b: str,
    path_c: str,
    path_a: str | None,
    required_transports: Sequence[str],
) -> dict[str, Any]:
    result = compare_paths(path_b=path_b, path_c=path_c, path_a=path_a)
    transports = result.get("transports")
    transports = transports if isinstance(transports, Mapping) else {}
    missing: list[dict[str, str]] = []
    for leg in ("path_b", "path_c"):
        counts = transports.get(leg)
        counts = counts if isinstance(counts, Mapping) else {}
        for transport in required_transports:
            count = counts.get(transport)
            if not isinstance(count, int) or count < 1:
                missing.append({"path": leg, "transport": transport})

    comparison = result.get("path_b_vs_c")
    comparison = comparison if isinstance(comparison, Mapping) else {}
    hard_mismatches = comparison.get("hard_mismatches")
    categories = Counter(
        str(item.get("category", "unspecified")) for item in hard_mismatches or [] if isinstance(item, Mapping)
    )
    base_pass = result.get("summary", {}).get("overall_pass") is True
    return {
        "passed": base_pass and not missing,
        "comparison_passed": base_pass,
        "required_transports": list(required_transports),
        "missing_coverage": missing,
        "transport_counts": {leg: transports.get(leg) for leg in ("path_a", "path_b", "path_c")},
        "hard_mismatch_count": int(comparison.get("hard_mismatch_count", 0) or 0),
        "hard_mismatch_categories": dict(sorted(categories.items())),
    }


def _tls_section(
    path_a_reference: str,
    path_a: str,
    path_c: str,
    required_transports: Sequence[str],
    *,
    min_samples: int,
) -> dict[str, Any]:
    result = analyze_tls_randomization_paths(
        path_a_reference,
        path_a,
        path_c,
        min_samples=min_samples,
    )
    transports = result.get("transports")
    transports = transports if isinstance(transports, Mapping) else {}
    summaries: dict[str, Any] = {}
    for transport in required_transports:
        item = transports.get(transport)
        item = item if isinstance(item, Mapping) else {}
        cohorts = item.get("cohorts")
        cohorts = cohorts if isinstance(cohorts, Mapping) else {}
        summaries[transport] = {
            "matches": item.get("matches"),
            "status": item.get("status", "unobserved"),
            "stable_profiles_match": item.get("stable_profiles_match"),
            "extension_order_matches_direct_variance": item.get("extension_order_matches_direct_variance"),
            "samples": {
                label: cohort.get("samples") if isinstance(cohort, Mapping) else None
                for label, cohort in (
                    ("path_a_reference", cohorts.get("path_a_reference")),
                    ("path_a", cohorts.get("path_a")),
                    ("path_c", cohorts.get("path_c")),
                )
            },
            "baseline_distance": item.get("baseline_a_reference_vs_a_distance"),
            "candidate_distance": item.get("candidate_a_vs_c_distance"),
            "acceptance_limit": item.get("acceptance_limit"),
        }
    return {
        "passed": bool(summaries) and all(item["matches"] is True for item in summaries.values()),
        "available": result.get("available") is True,
        "minimum_samples": min_samples,
        "required_transports": list(required_transports),
        "transports": summaries,
    }


def _dimension_verdicts(comparison: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(comparison, Mapping):
        return {}
    dimensions = comparison.get("dimensions")
    if not isinstance(dimensions, Mapping):
        return {}
    return {
        str(name): {
            "observed": item.get("observed"),
            "match": item.get("match"),
        }
        for name, item in dimensions.items()
        if isinstance(item, Mapping)
    }


def _http2_section(path_a_reference: str, path_a: str, path_c: str) -> dict[str, Any]:
    try:
        result = compare_profiles(
            load_h2_records(Path(path_a)),
            load_h2_records(Path(path_c)),
            load_h2_records(Path(path_a_reference)),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {
            "passed": False,
            "available": False,
            "error": type(exc).__name__,
            "a_reference_vs_a": {},
            "a_vs_c": {},
        }
    direct = result.get("a_reference_vs_a")
    routed = result.get("a_vs_c")
    direct_pass = isinstance(direct, Mapping) and direct.get("all_observed_match") is True
    routed_pass = isinstance(routed, Mapping) and routed.get("all_observed_match") is True
    return {
        "passed": direct_pass and routed_pass,
        "available": True,
        "error": None,
        "a_reference_vs_a": _dimension_verdicts(direct),
        "a_vs_c": _dimension_verdicts(routed),
    }


def build_composite_gate(
    *,
    semantic_path_b: str,
    semantic_path_c: str,
    tls_path_a_reference: str,
    tls_path_a: str,
    tls_path_c: str,
    h2_path_a_reference: str,
    h2_path_a: str,
    h2_path_c: str,
    semantic_path_a: str | None = None,
    required_semantic_transports: Sequence[str] = DEFAULT_SEMANTIC_TRANSPORTS,
    required_tls_transports: Sequence[str] = DEFAULT_TLS_TRANSPORTS,
    tls_min_samples: int = 20,
    failure_root: str | None = None,
    failure_baseline: str | Path = DEFAULT_FAILURE_BASELINE,
    required_failure_scenarios: Sequence[str] = DEFAULT_FAILURE_SCENARIOS,
    require_failure_matrix: bool = False,
) -> dict[str, Any]:
    inputs = {
        "semantic_path_b": semantic_path_b,
        "semantic_path_c": semantic_path_c,
        "tls_path_a_reference": tls_path_a_reference,
        "tls_path_a": tls_path_a,
        "tls_path_c": tls_path_c,
        "h2_path_a_reference": h2_path_a_reference,
        "h2_path_a": h2_path_a,
        "h2_path_c": h2_path_c,
    }
    if semantic_path_a is not None:
        inputs["semantic_path_a"] = semantic_path_a

    evidence = [file_attestation(label, path) for label, path in sorted(inputs.items())]
    semantic = _semantic_section(
        semantic_path_b,
        semantic_path_c,
        semantic_path_a,
        required_semantic_transports,
    )
    tls = _tls_section(
        tls_path_a_reference,
        tls_path_a,
        tls_path_c,
        required_tls_transports,
        min_samples=tls_min_samples,
    )
    http2 = _http2_section(h2_path_a_reference, h2_path_a, h2_path_c)
    if failure_root is not None:
        failure = build_failure_matrix_gate(
            failure_root,
            baseline=failure_baseline,
            required_scenarios=required_failure_scenarios,
        )
        failure["required"] = True
        evidence.extend(failure.get("evidence", []))
    elif require_failure_matrix:
        failure = {
            "passed": False,
            "available": False,
            "required": True,
            "error": "failure_root_not_supplied",
            "required_scenarios": list(required_failure_scenarios),
            "scenarios": {},
            "evidence": [],
        }
    else:
        failure = {
            "passed": True,
            "available": False,
            "required": False,
            "error": "not_requested",
            "required_scenarios": [],
            "scenarios": {},
            "evidence": [],
        }
    timing_paths = {
        "path_b": semantic_path_b,
        "path_c": semantic_path_c,
    }
    if semantic_path_a is not None:
        timing_paths["path_a"] = semantic_path_a
    timing = {label: _timing_summary(path) for label, path in timing_paths.items()}
    evidence_available = all(item["available"] is True for item in evidence)
    overall_pass = evidence_available and all(section["passed"] is True for section in (semantic, tls, http2, failure))
    return {
        "schema_version": 1,
        "overall_pass": overall_pass,
        "strict_exit_code": 0 if overall_pass else 2,
        "sections": {
            "semantic": semantic,
            "tls": tls,
            "http2": http2,
            "failure_matrix": failure,
        },
        "timing_informational": {
            "gate": False,
            "reason": "separate invocations and scheduling variance require repeated statistical cohorts",
            "paths": timing,
        },
        "evidence": evidence,
    }


def _mark(value: Any) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "N/A"


def render_markdown(result: Mapping[str, Any]) -> str:
    sections = result.get("sections")
    sections = sections if isinstance(sections, Mapping) else {}
    lines = [
        "# Composite Codex traffic parity gate",
        "",
        f"Overall: **{_mark(result.get('overall_pass'))}**",
        "",
        "| Section | Verdict |",
        "|---|---:|",
    ]
    for name in ("semantic", "tls", "http2", "failure_matrix"):
        section = sections.get(name)
        lines.append(f"| `{name}` | {_mark(section.get('passed') if isinstance(section, Mapping) else None)} |")

    semantic = sections.get("semantic")
    if isinstance(semantic, Mapping):
        lines.extend(
            [
                "",
                "## Required semantic coverage",
                "",
                "```json",
                json.dumps(
                    {
                        "required_transports": semantic.get("required_transports"),
                        "transport_counts": semantic.get("transport_counts"),
                        "missing_coverage": semantic.get("missing_coverage"),
                        "hard_mismatch_count": semantic.get("hard_mismatch_count"),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                "```",
            ]
        )

    tls = sections.get("tls")
    if isinstance(tls, Mapping):
        lines.extend(
            [
                "",
                "## TLS cohorts",
                "",
                "| Transport | Samples A′ / A / C | Stable | Order | Verdict |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        transports = tls.get("transports")
        if isinstance(transports, Mapping):
            for name, item in transports.items():
                if not isinstance(item, Mapping):
                    continue
                samples = item.get("samples")
                samples = samples if isinstance(samples, Mapping) else {}
                sample_text = (
                    f"{samples.get('path_a_reference', '—')} / "
                    f"{samples.get('path_a', '—')} / {samples.get('path_c', '—')}"
                )
                lines.append(
                    f"| `{name}` | {sample_text} | {_mark(item.get('stable_profiles_match'))} | "
                    f"{_mark(item.get('extension_order_matches_direct_variance'))} | {_mark(item.get('matches'))} |"
                )

    http2 = sections.get("http2")
    if isinstance(http2, Mapping):
        lines.extend(["", "## Raw HTTP/2", "", "| Dimension | A′ ↔ A | A ↔ C |", "|---|---:|---:|"])
        direct = http2.get("a_reference_vs_a")
        routed = http2.get("a_vs_c")
        direct = direct if isinstance(direct, Mapping) else {}
        routed = routed if isinstance(routed, Mapping) else {}
        for name in sorted(set(direct) | set(routed)):
            left = direct.get(name)
            right = routed.get(name)
            lines.append(
                f"| `{name}` | {_mark(left.get('match') if isinstance(left, Mapping) else None)} | "
                f"{_mark(right.get('match') if isinstance(right, Mapping) else None)} |"
            )

    failure = sections.get("failure_matrix")
    if isinstance(failure, Mapping) and failure.get("required") is True:
        lines.extend(
            [
                "",
                "## Controlled failure matrix",
                "",
                "| Scenario | Attempts A / B | Final relation | Verdict |",
                "|---|---:|---|---:|",
            ]
        )
        scenarios = failure.get("scenarios")
        if isinstance(scenarios, Mapping):
            for name, item in scenarios.items():
                if not isinstance(item, Mapping):
                    continue
                attempts = item.get("attempt_counts")
                attempts = attempts if isinstance(attempts, Mapping) else {}
                lines.append(
                    f"| `{name}` | {attempts.get('path_a', '—')} / {attempts.get('path_b', '—')} | "
                    f"`{item.get('final_relation') or '—'}` | {_mark(item.get('passed'))} |"
                )

    lines.extend(
        [
            "",
            "## Timing (informational only)",
            "",
            "Timing does not participate in the strict verdict. It requires repeated statistical cohorts.",
            "",
            "```json",
            json.dumps(result.get("timing_informational", {}), indent=2, sort_keys=True),
            "```",
            "",
            "## Evidence attestations",
            "",
            "| Label | Bytes | SHA-256 | Available |",
            "|---|---:|---|---:|",
        ]
    )
    for item in result.get("evidence", []):
        if not isinstance(item, Mapping):
            continue
        lines.append(
            f"| `{item.get('label')}` | {item.get('bytes') if item.get('bytes') is not None else '—'} | "
            f"`{item.get('sha256') or '—'}` | {_mark(item.get('available'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic-path-a")
    parser.add_argument("--semantic-path-b", required=True)
    parser.add_argument("--semantic-path-c", required=True)
    parser.add_argument("--tls-path-a-reference", required=True)
    parser.add_argument("--tls-path-a", required=True)
    parser.add_argument("--tls-path-c", required=True)
    parser.add_argument("--h2-path-a-reference", required=True)
    parser.add_argument("--h2-path-a", required=True)
    parser.add_argument("--h2-path-c", required=True)
    parser.add_argument("--require-semantic-transport", action="append", dest="required_semantic_transports")
    parser.add_argument("--require-tls-transport", action="append", dest="required_tls_transports")
    parser.add_argument("--tls-min-samples", type=int, default=20)
    parser.add_argument("--failure-root")
    parser.add_argument("--failure-baseline", default=str(DEFAULT_FAILURE_BASELINE))
    parser.add_argument("--require-failure-scenario", action="append", dest="required_failure_scenarios")
    parser.add_argument("--require-failure-matrix", action="store_true")
    parser.add_argument("--output", type=Path, required=True, help="Markdown output")
    parser.add_argument("--json-output", type=Path, help="Machine-readable output")
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_composite_gate(
        semantic_path_a=args.semantic_path_a,
        semantic_path_b=args.semantic_path_b,
        semantic_path_c=args.semantic_path_c,
        tls_path_a_reference=args.tls_path_a_reference,
        tls_path_a=args.tls_path_a,
        tls_path_c=args.tls_path_c,
        h2_path_a_reference=args.h2_path_a_reference,
        h2_path_a=args.h2_path_a,
        h2_path_c=args.h2_path_c,
        required_semantic_transports=args.required_semantic_transports or DEFAULT_SEMANTIC_TRANSPORTS,
        required_tls_transports=args.required_tls_transports or DEFAULT_TLS_TRANSPORTS,
        tls_min_samples=args.tls_min_samples,
        failure_root=args.failure_root,
        failure_baseline=args.failure_baseline,
        required_failure_scenarios=args.required_failure_scenarios or DEFAULT_FAILURE_SCENARIOS,
        require_failure_matrix=args.require_failure_matrix,
    )
    atomic_write_text(args.output, render_markdown(result))
    if args.json_output is not None:
        atomic_write_json(args.json_output, result)
    print(f"Composite parity gate: {_mark(result['overall_pass'])}; report: {args.output}")
    return int(result["strict_exit_code"]) if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
