"""Render the Codex three-path traffic comparison as Markdown."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.traffic_analysis.compare import compare_paths
    from scripts.traffic_analysis.tls_randomization import DEFAULT_MIN_SAMPLES, TLS_TRANSPORTS
except ModuleNotFoundError:  # Allow direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.compare import compare_paths
    from scripts.traffic_analysis.tls_randomization import DEFAULT_MIN_SAMPLES, TLS_TRANSPORTS


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _mark(value: Any) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "N/A"


def _transport_summary(transports: Mapping[str, Any]) -> str:
    lines = [
        "| Path | Role | http_json | http_sse | websocket | Other |",
        "|---|---|---:|---:|---:|---:|",
    ]
    roles = {
        "path_a": "direct baseline",
        "path_b": "client → LB",
        "path_c": "LB → upstream",
    }
    for key in ("path_a", "path_b", "path_c"):
        counts = transports.get(key)
        if not isinstance(counts, Mapping):
            lines.append(f"| {key[-1].upper()} | {roles[key]} | — | — | — | — |")
            continue
        known = sum(int(counts.get(name, 0) or 0) for name in ("http_json", "http_sse", "websocket"))
        total = sum(int(value or 0) for value in counts.values())
        lines.append(
            f"| {key[-1].upper()} | {roles[key]} | {_cell(counts.get('http_json', 0))} | "
            f"{_cell(counts.get('http_sse', 0))} | {_cell(counts.get('websocket', 0))} | {total - known} |"
        )
    return "\n".join(lines)


def _bc_turn_table(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| Turn | B transport | C transport | Translation | Request | Response | Events | Usage | Tools | Terminal | "
        "Verdict |",
        "|---:|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        path_b = row.get("path_b") or {}
        path_c = row.get("path_c") or {}
        checks = row.get("checks") or {}
        transport = checks.get("transport") or {}
        material = checks.get("material_events") or {}
        event_result = material.get("payloads_match")
        if event_result is None:
            event_result = material.get("order_matches")
        lines.append(
            f"| {_cell(row.get('turn'))} | {_cell(path_b.get('transport'))} | {_cell(path_c.get('transport'))} | "
            f"{_cell(transport.get('translated')) if transport else 'N/A'} | "
            f"{_mark(checks.get('semantic_request'))} | {_mark(checks.get('response_semantics'))} | "
            f"{_mark(event_result)} | {_mark(checks.get('usage'))} | {_mark(checks.get('tool_calls'))} | "
            f"{_mark(checks.get('terminal_class'))} | "
            f"{_mark(row.get('passed'))} |"
        )
    if not rows:
        lines.append("| — | — | — | — | — | — | — | — | — | — | FAIL |")
    return "\n".join(lines)


def _mismatch_table(mismatches: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| Turn | Category | Detail | Path B | Path C |",
        "|---:|---|---|---|---|",
    ]
    for mismatch in mismatches:
        path_b = mismatch.get("path_b")
        path_c = mismatch.get("path_c")
        # Payload diffs can contain prompts.  Keep the report readable; the JSON
        # result remains the detailed machine-readable artifact.
        left = _cell(path_b)
        right = _cell(path_c)
        if len(left) > 240:
            left = left[:237] + "…"
        if len(right) > 240:
            right = right[:237] + "…"
        lines.append(
            f"| {_cell(mismatch.get('turn'))} | {_cell(mismatch.get('category'))} | "
            f"{_cell(mismatch.get('detail'))} | {left} | {right} |"
        )
    if not mismatches:
        lines.append("| — | — | No hard B/C mismatches. | — | — |")
    return "\n".join(lines)


def _raw_difference_table(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| Turn | Raw request differences |",
        "|---:|---:|",
    ]
    any_differences = False
    for row in rows:
        differences = row.get("raw_request_differences") or []
        if differences:
            any_differences = True
            lines.append(f"| {_cell(row.get('turn'))} | {len(differences)} |")
    if not any_differences:
        lines.append("| — | 0 |")
    return "\n".join(lines)


def _baseline_table(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| Turn | A transport | C transport | Request profile | Event order | Usage delta (C−A) | Terminal |",
        "|---:|---|---|---|---|---|---|",
    ]
    for row in rows:
        path_a = row.get("path_a") or {}
        path_c = row.get("path_c") or {}
        observations = row.get("protocol_observations") or {}
        lines.append(
            f"| {_cell(row.get('turn'))} | {_cell(path_a.get('transport'))} | {_cell(path_c.get('transport'))} | "
            f"{_mark(observations.get('semantic_request_matches'))} | "
            f"{_mark(observations.get('material_event_order_matches'))} | "
            f"{_cell(observations.get('usage_delta'))} | "
            f"{_mark(observations.get('terminal_class_matches'))} |"
        )
    if not rows:
        lines.append("| — | — | — | — | — | — | — |")
    return "\n".join(lines)


def _diagnostics_table(diagnostics: Mapping[str, Any]) -> str:
    lines = [
        "| Path | Parse errors | Orphan WebSocket frames | Incomplete HTTP turns | Incomplete WebSocket turns |",
        "|---|---:|---:|---|---|",
    ]
    for key in ("path_a", "path_b", "path_c"):
        item = diagnostics.get(key)
        if not isinstance(item, Mapping):
            lines.append(f"| {key[-1].upper()} | — | — | — | — |")
            continue
        lines.append(
            f"| {key[-1].upper()} | {len(item.get('parse_errors') or [])} | "
            f"{len(item.get('orphan_websocket_messages') or [])} | "
            f"{_cell(item.get('incomplete_http_turns') or [])} | "
            f"{_cell(item.get('incomplete_websocket_turns') or [])} |"
        )
    return "\n".join(lines)


def _failure_path_table(
    analysis: Mapping[str, Any],
    *,
    left_label: str,
    right_label: str,
) -> str:
    left_key = f"path_{left_label.lower()}"
    right_key = f"path_{right_label.lower()}"
    lines = [
        f"| Turn | {left_label} outcome | {right_label} outcome | {left_label} status | {right_label} status | "
        f"{left_label} retry | {right_label} retry | {left_label} terminal | {right_label} terminal | "
        f"{left_label} complete | {right_label} complete | {left_label} incomplete reason | "
        f"{right_label} incomplete reason | {left_label} network error | {right_label} network error | Relation | "
        "Compatible |",
        "|---:|---|---|---:|---:|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in analysis.get("turns") or []:
        path_left = row.get(left_key) or {}
        path_right = row.get(right_key) or {}
        lines.append(
            f"| {_cell(row.get('turn'))} | {_cell(path_left.get('class'))} | {_cell(path_right.get('class'))} | "
            f"{_cell(path_left.get('http_status'))} | {_cell(path_right.get('http_status'))} | "
            f"{_cell(path_left.get('retry_after'))} | {_cell(path_right.get('retry_after'))} | "
            f"{_cell(path_left.get('terminal_class'))} | {_cell(path_right.get('terminal_class'))} | "
            f"{_cell(path_left.get('complete'))} | {_cell(path_right.get('complete'))} | "
            f"{_cell(path_left.get('incomplete_reason'))} | {_cell(path_right.get('incomplete_reason'))} | "
            f"{_cell(path_left.get('network_error_category'))} | "
            f"{_cell(path_right.get('network_error_category'))} | "
            f"{_cell(row.get('relation'))} | {_mark(row.get('compatible'))} |"
        )
    if not analysis.get("turns"):
        lines.append("| — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | unobserved | N/A |")
    return "\n".join(lines)


def _server_observable_table(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| Turn | Protocol/ALPN | TLS | Identity headers | Header order | Header casing | SSE framing | "
        "WebSocket handshake | Observed source | ASN |",
        "|---:|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        comparison = row.get("comparison") or {}
        dimensions = comparison.get("dimensions") or {}
        lines.append(
            f"| {_cell(row.get('turn'))} | {_mark((dimensions.get('protocol') or {}).get('matches'))} | "
            f"{_mark((dimensions.get('tls') or {}).get('matches'))} | "
            f"{_mark((dimensions.get('identity') or {}).get('matches'))} | "
            f"{_mark((dimensions.get('header_order') or {}).get('matches'))} | "
            f"{_mark((dimensions.get('header_casing') or {}).get('matches'))} | "
            f"{_mark((dimensions.get('sse') or {}).get('matches'))} | "
            f"{_mark((dimensions.get('websocket') or {}).get('matches'))} | "
            f"{_mark((dimensions.get('observed_source') or {}).get('matches'))} | "
            f"{_mark((dimensions.get('asn') or {}).get('matches'))} |"
        )
    if not rows:
        lines.append("| — | — | — | — | — | — | — | — | — | — |")
    return "\n".join(lines)


def _tls_randomization_table(analysis: Mapping[str, Any]) -> str:
    lines = [
        "| Transport | A′ samples | A samples | C samples | Stable TLS | A′↔A distance | A↔C distance | "
        "95% limit | Entropy A′ / A / C | Verdict |",
        "|---|---:|---:|---:|---|---:|---:|---:|---|---|",
    ]
    transports = analysis.get("transports") if isinstance(analysis, Mapping) else {}
    for transport in TLS_TRANSPORTS:
        item = transports.get(transport, {}) if isinstance(transports, Mapping) else {}
        cohorts = item.get("cohorts", {}) if isinstance(item, Mapping) else {}
        reference = cohorts.get("path_a_reference", {}) if isinstance(cohorts, Mapping) else {}
        direct = cohorts.get("path_a", {}) if isinstance(cohorts, Mapping) else {}
        candidate = cohorts.get("path_c", {}) if isinstance(cohorts, Mapping) else {}
        entropy = " / ".join(
            _cell(cohort.get("pairwise_order_entropy"))
            for cohort in (reference, direct, candidate)
            if isinstance(cohort, Mapping)
        )
        lines.append(
            f"| {transport} | {_cell(reference.get('samples'))} | {_cell(direct.get('samples'))} | "
            f"{_cell(candidate.get('samples'))} | {_mark(item.get('stable_profiles_match'))} | "
            f"{_cell(item.get('baseline_a_reference_vs_a_distance'))} | "
            f"{_cell(item.get('candidate_a_vs_c_distance'))} | {_cell(item.get('acceptance_limit'))} | "
            f"{entropy or '—'} | {_mark(item.get('matches'))} |"
        )
    return "\n".join(lines)


def build_report(result: Mapping[str, Any]) -> str:
    """Build a Markdown report from :func:`compare_paths` output."""

    summary = result.get("summary") or {}
    bc = result.get("path_b_vs_c") or {}
    baseline = result.get("path_a_baseline") or {}
    server_observable = result.get("server_observable_a_vs_c") or {}
    counts = result.get("turn_counts") or {}
    paths = result.get("paths") or {}
    passed = bool(summary.get("overall_pass"))
    verdict = "PASS" if passed else "FAIL"

    lines = [
        "# Codex Network Traffic Parity Report",
        "",
        "## Verdict",
        "",
        f"**{verdict}** — {int(summary.get('hard_mismatch_count', 0) or 0)} hard B/C mismatch(es).",
        "",
        "Path B and C are same-run observations and are the strict parity gate. Path A is a separately generated "
        "direct-upstream baseline; its generated content, event ordering, and token counts are informational only.",
        "",
        "## Inputs",
        "",
        "| Path | Capture | Turns |",
        "|---|---|---:|",
        f"| A′ | {_cell(paths.get('path_a_reference'))} | TLS calibration only |",
        f"| A | {_cell(paths.get('path_a'))} | {_cell(counts.get('path_a'))} |",
        f"| B | {_cell(paths.get('path_b'))} | {_cell(counts.get('path_b'))} |",
        f"| C | {_cell(paths.get('path_c'))} | {_cell(counts.get('path_c'))} |",
        "",
        "## Transport Breakdown",
        "",
        _transport_summary(result.get("transports") or {}),
        "",
        "A B↔C transport transition is shown explicitly. A supported HTTP JSON/SSE/WebSocket translation is not a "
        "failure by itself; loss of material response semantics is.",
        "",
        "## Same-run B↔C Parity",
        "",
        _bc_turn_table(bc.get("turns") or []),
        "",
        "`N/A` for Events means one side is HTTP JSON, so parity is evaluated from its aggregate terminal, usage, and "
        "tool semantics rather than a streaming event sequence.",
        "",
        "## Failure-path Outcomes",
        "",
        "### End-to-end A↔B",
        "",
        _failure_path_table(
            result.get("failure_path_a_vs_b") or {},
            left_label="A",
            right_label="B",
        ),
        "",
        "The A↔B table compares direct Codex with the client-visible result through codex-lb. Attempt counts and the "
        "final outcome remain informational because the two paths are separately generated runs.",
        "",
        "### Same-run B↔C",
        "",
        _failure_path_table(
            result.get("failure_path_b_vs_c") or {},
            left_label="B",
            right_label="C",
        ),
        "",
        "Failure outcomes are informational. `failure_translation` means both legs failed through different wire "
        "forms; it does not turn an incomplete lifecycle into a strict pass. Raw transport exception messages are "
        "never retained.",
        "",
        "### Hard mismatches",
        "",
        _mismatch_table(bc.get("hard_mismatches") or []),
        "",
        "### Raw request observations",
        "",
        _raw_difference_table(bc.get("turns") or []),
        "",
        "Raw hop differences are observations. Only the preserved semantic request fields (`model`, `service_tier`, "
        "`reasoning`, tool controls, continuity fields, output controls, and ordered prompt/tool content fingerprints) "
        "participate in the hard gate. Fields that the upstream adapter may intentionally omit are hard-compared when "
        "present on both legs and otherwise remain an explicit one-sided observation.",
        "",
        "## Capture Diagnostics",
        "",
        _diagnostics_table(result.get("diagnostics") or {}),
        "",
        "Unattached server frames and parse errors fail B/C; client control frames remain informational diagnostics.",
        "",
        "## Direct Path A Baseline",
        "",
    ]
    if baseline.get("available"):
        lines.extend(
            [
                _baseline_table(baseline.get("turns") or []),
                "",
                "Differences here do not change the verdict because Path A is not the same model generation as B/C.",
            ]
        )
    else:
        lines.append("Path A was not supplied.")

    lines.extend(
        [
            "",
            "## Server-observable A↔C Profile",
            "",
            _server_observable_table(server_observable.get("turns") or []),
            "",
            "These dimensions are independent and informational: matching identity headers never upgrades a TLS or "
            "protocol mismatch to full parity. Observed source is N/A unless A and C attest the same observer. An "
            "intercept-boundary match is not proof of the public source IP or ASN seen by OpenAI. Public source-IP "
            "and ASN claims require a controlled origin; ASN also requires matching offline-database provenance. "
            "Header order/casing describes decoded field names only and does not attest HPACK or HTTP/2 frames.",
            "",
            "## TLS Extension-order Randomization",
            "",
            _tls_randomization_table(result.get("tls_randomization_a_vs_c") or {}),
            "",
            "A′ and A are independent direct-Codex cohorts. Stable TLS capability fields are exact; only extension "
            "order is evaluated statistically. The A↔C pairwise-order distance must remain within the larger of the "
            "observed A′↔A distance and the deterministic direct-only bootstrap 95% limit. Raw JA3 values remain "
            "informational. N/A means at least one cohort lacks enough independent ClientHellos.",
        ]
    )

    policy = result.get("policy") or {}
    lines.extend(
        [
            "",
            "## Comparison Policy",
            "",
            f"- Ignored non-material event types: {_cell(policy.get('ignored_event_types'))}",
            f"- Placeholder-normalized fields: {_cell(policy.get('normalized_fields'))}",
            "- Volatile IDs are not deleted: repeated response/item/call references retain a shared placeholder, so "
            "correlation breaks remain visible.",
            "- Credential header values are redacted from comparison output.",
            "- Event order, usage, tool identities/arguments, terminal class, model, service tier, and reasoning "
            "remain material.",
            "",
        ]
    )
    return "\n".join(lines)


def report_from_paths(
    path_b: str,
    path_c: str,
    path_a: str | None = None,
    *,
    path_a_reference: str | None = None,
    tls_min_samples: int = DEFAULT_MIN_SAMPLES,
) -> tuple[str, dict[str, Any]]:
    result = compare_paths(
        path_b=path_b,
        path_c=path_c,
        path_a=path_a,
        path_a_reference=path_a_reference,
        tls_min_samples=tls_min_samples,
    )
    return build_report(result), result


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Markdown Codex traffic parity report.")
    parser.add_argument("--path-a", help="Optional direct-upstream baseline capture")
    parser.add_argument(
        "--path-a-reference",
        help="Optional second direct capture used to calibrate TLS extension-order variance",
    )
    parser.add_argument("--path-b", required=True, help="Required client-to-LB capture")
    parser.add_argument("--path-c", required=True, help="Required LB-to-upstream capture")
    parser.add_argument("--output", required=True, help="Markdown report output path")
    parser.add_argument("--json-output", help="Optional machine-readable comparison output path")
    parser.add_argument(
        "--tls-min-samples",
        type=int,
        default=DEFAULT_MIN_SAMPLES,
        help=f"Minimum deduplicated ClientHellos per TLS cohort (default: {DEFAULT_MIN_SAMPLES})",
    )
    parser.add_argument("--strict", action="store_true", help="Exit nonzero on a hard B/C mismatch")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    report, result = report_from_paths(
        args.path_b,
        args.path_c,
        args.path_a,
        path_a_reference=args.path_a_reference,
        tls_min_samples=args.tls_min_samples,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")

    if args.json_output:
        json_output = Path(args.json_output)
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Report written: {output}")
    return int(result["summary"]["strict_exit_code"]) if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
