"""Compare privacy-safe raw HTTP/2 profiles from controlled observer runs."""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

STABLE_DIMENSIONS = (
    "initial_settings",
    "connection_control_shape",
    "header_name_order",
    "header_name_casing",
    "request_data_segmentation",
    "stream_and_reuse_pattern",
)

DEFAULT_MAX_FRAME_SIZE = 16_384


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if isinstance(value, dict) and value.get("kind") == "http2_wire_request":
                records.append(value)
    return records


def _grouped(records: Sequence[Mapping[str, Any]]) -> OrderedDict[str, list[Mapping[str, Any]]]:
    groups: OrderedDict[str, list[Mapping[str, Any]]] = OrderedDict()
    for record in records:
        connection_id = record.get("connection_id")
        if not isinstance(connection_id, str):
            continue
        groups.setdefault(connection_id, []).append(record)
    for connection_records in groups.values():
        connection_records.sort(key=lambda record: int(record.get("request_sequence", 0)))
    return groups


def _ordered(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [record for connection_records in _grouped(records).values() for record in connection_records]


def _per_connection_value(records: Sequence[Mapping[str, Any]], key: str) -> Any:
    values = []
    for connection_records in _grouped(records).values():
        value = connection_records[0].get(key)
        if value is None:
            return None
        values.append(value)
    return values or None


def _control_shape(records: Sequence[Mapping[str, Any]]) -> Any:
    connections = _per_connection_value(records, "connection_control_frames")
    if not isinstance(connections, list) or any(not isinstance(frames, list) for frames in connections):
        return None
    return [
        [
            {key: frame[key] for key in ("type", "flags", "stream_id", "length", "window_increment") if key in frame}
            for frame in frames
            if isinstance(frame, Mapping) and not (frame.get("type") == "SETTINGS" and int(frame.get("flags", 0)) & 0x1)
        ]
        for frames in connections
    ]


def _header_names(records: Sequence[Mapping[str, Any]], *, casefold: bool) -> Any:
    sequences: list[list[str]] = []
    for record in _ordered(records):
        request = record.get("request")
        if not isinstance(request, Mapping) or not isinstance(request.get("header_names"), list):
            return None
        names = [str(name) for name in request["header_names"]]
        sequences.append([name.casefold() for name in names] if casefold else names)
    return sequences or None


def _header_casing(records: Sequence[Mapping[str, Any]]) -> Any:
    """Project only non-lowercase spellings so order/set changes stay separate."""

    sequences: list[list[str]] = []
    for record in _ordered(records):
        request = record.get("request")
        if not isinstance(request, Mapping) or not isinstance(request.get("header_names"), list):
            return None
        names = request["header_names"]
        if not all(isinstance(name, str) for name in names):
            return None
        sequences.append([name for name in names if name != name.casefold()])
    return sequences or None


def _stream_pattern(records: Sequence[Mapping[str, Any]]) -> Any:
    groups = _grouped(records)
    if not groups:
        return None
    if any(not isinstance(record.get("stream_id"), int) for group in groups.values() for record in group):
        return None
    return [
        {
            "stream_ids": [record["stream_id"] for record in connection_records],
            "reused": [bool(record.get("connection_reused")) for record in connection_records],
        }
        for connection_records in groups.values()
    ]


def _request_max_frame_size(record: Mapping[str, Any]) -> int | None:
    settings = record.get("initial_settings")
    if settings is None:
        return None
    if not isinstance(settings, list):
        return None
    for setting in settings:
        if not isinstance(setting, Mapping):
            return None
        if setting.get("id") != 5:
            continue
        value = setting.get("value")
        return value if isinstance(value, int) and value > 0 else None
    return DEFAULT_MAX_FRAME_SIZE


def _data_segmentation(records: Sequence[Mapping[str, Any]]) -> Any:
    """Normalize DATA chunking without comparing bytes or variable tail size."""

    sequences: list[list[dict[str, Any]]] = []
    for record in _ordered(records):
        frames = record.get("request_frames")
        max_frame_size = _request_max_frame_size(record)
        if not isinstance(frames, list) or max_frame_size is None:
            return None
        sequence: list[dict[str, Any]] = []
        for frame in frames:
            if not isinstance(frame, Mapping):
                return None
            if frame.get("type") != "DATA":
                continue
            length = frame.get("length")
            flags = frame.get("flags")
            if not isinstance(length, int) or not isinstance(flags, int):
                return None
            sequence.append(
                {
                    "size_class": "max" if length == max_frame_size else "partial",
                    "end_stream": bool(flags & 0x1),
                    "padded": bool(flags & 0x8),
                }
            )
        sequences.append(sequence)
    return sequences or None


def profile(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = _ordered(records)
    fragments: list[dict[str, Any]] = []
    for record in ordered:
        for frame in record.get("request_frames", []):
            if not isinstance(frame, Mapping) or frame.get("type") not in {"HEADERS", "CONTINUATION"}:
                continue
            fragments.append(
                {
                    "type": frame.get("type"),
                    "length": frame.get("length"),
                    "sha256": frame.get("fragment_sha256"),
                }
            )
    return {
        "request_count": len(ordered),
        "initial_settings": _per_connection_value(ordered, "initial_settings"),
        "connection_control_shape": _control_shape(ordered),
        "header_name_order": _header_names(ordered, casefold=True),
        "header_name_casing": _header_casing(ordered),
        "request_data_segmentation": _data_segmentation(ordered),
        "stream_and_reuse_pattern": _stream_pattern(ordered),
        "hpack_fragments": fragments or None,
    }


def compare_profiles(
    path_a_records: Sequence[Mapping[str, Any]],
    path_c_records: Sequence[Mapping[str, Any]],
    path_a_reference_records: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    a = profile(path_a_records)
    c = profile(path_c_records)
    a_reference = profile(path_a_reference_records) if path_a_reference_records is not None else None

    def comparison(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
        dimensions: dict[str, Any] = {}
        for name in STABLE_DIMENSIONS:
            left_value = left.get(name)
            right_value = right.get(name)
            observed = left_value is not None and right_value is not None
            dimensions[name] = {
                "observed": observed,
                "match": left_value == right_value if observed else None,
                "left": left_value,
                "right": right_value,
                "reason": None if observed else "missing evidence",
            }
        return {
            "dimensions": dimensions,
            "all_observed_match": all(item["match"] is True for item in dimensions.values()),
        }

    result: dict[str, Any] = {
        "path_a": a,
        "path_c": c,
        "a_vs_c": comparison(a, c),
        "hpack_informational": {"path_a": a["hpack_fragments"], "path_c": c["hpack_fragments"]},
    }
    if a_reference is not None:
        result["path_a_reference"] = a_reference
        result["a_reference_vs_a"] = comparison(a_reference, a)
        result["hpack_informational"]["path_a_reference"] = a_reference["hpack_fragments"]
    return result


def _verdict(item: Mapping[str, Any]) -> str:
    if not item.get("observed"):
        return "N/A"
    return "MATCH" if item.get("match") else "DIFF"


def render_markdown(result: Mapping[str, Any]) -> str:
    direct_reference = result.get("a_reference_vs_a")
    lines = [
        "# Raw HTTP/2 profile comparison",
        "",
        "Stable dimensions are exact comparisons. Missing evidence is N/A, not a pass. "
        "HPACK digests are opaque informational evidence and are never a parity gate.",
        "",
        "| Dimension | A′ ↔ A | A ↔ C |",
        "|---|---:|---:|",
    ]
    a_vs_c = result["a_vs_c"]
    for name in STABLE_DIMENSIONS:
        reference_verdict = "N/A"
        if isinstance(direct_reference, Mapping):
            reference_verdict = _verdict(direct_reference["dimensions"][name])
        lines.append(f"| `{name}` | {reference_verdict} | {_verdict(a_vs_c['dimensions'][name])} |")
    lines.extend(["", "## Informational HPACK fragments", "", "```json"])
    lines.append(json.dumps(result["hpack_informational"], indent=2, sort_keys=True))
    lines.extend(["```", ""])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path-a", type=Path, required=True)
    parser.add_argument("--path-c", type=Path, required=True)
    parser.add_argument("--path-a-reference", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="Markdown report output")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--strict", action="store_true", help="exit 2 unless every stable A/C dimension matches")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = compare_profiles(
        load_records(args.path_a),
        load_records(args.path_c),
        load_records(args.path_a_reference) if args.path_a_reference else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_markdown(result), encoding="utf-8")
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.strict and not result["a_vs_c"]["all_observed_match"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
