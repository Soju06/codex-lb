from __future__ import annotations

import hashlib
import hmac
import importlib
import itertools
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

from scripts.traffic_analysis import compare as compare_module
from scripts.traffic_analysis import generate_report as report_module
from scripts.traffic_analysis import tls_randomization
from scripts.traffic_analysis.compare import compare_paths, compare_turns
from scripts.traffic_analysis.generate_report import build_report
from scripts.traffic_analysis.protocol import HTTP_JSON, HTTP_SSE, ProtocolEvent, parse_sse, parse_websocket_data
from scripts.traffic_analysis.turns import extract_turns, extract_turns_with_diagnostics


def _completed(response_id: Any, *, input_tokens: int = 8) -> dict[str, Any]:
    return {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "status": "completed",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": 2,
                "total_tokens": input_tokens + 2,
                "input_tokens_details": {"cached_tokens": 3},
                "output_tokens_details": {"reasoning_tokens": 1},
            },
        },
    }


def _http_sse_record(
    *,
    flow_id: str = "http-1",
    input_tokens: int = 8,
    include_tool: bool = False,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = [
        {
            "event": "response.created",
            "data": {"type": "response.created", "response": {"id": "resp_b", "status": "in_progress"}},
        }
    ]
    if include_tool:
        events.append(
            {
                "event": "response.output_item.done",
                "data": {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "id": "item_b",
                        "type": "function_call",
                        "call_id": "call_b",
                        "name": "lookup",
                        "arguments": '{"city":"Seoul"}',
                    },
                },
            }
        )
    events.append({"event": "response.completed", "data": _completed("resp_b", input_tokens=input_tokens)})
    return {
        "kind": "http",
        "transport": "http_sse",
        "flow_id": flow_id,
        "request": {
            "method": "POST",
            "url": "http://lb.test/v1/responses",
            "headers": {"content-type": "application/json"},
            "body": {
                "model": "gpt-5.4",
                "service_tier": "priority",
                "reasoning": {"effort": "high"},
                "tools": [{"type": "function", "name": "lookup"}] if include_tool else [],
                "input": "hello",
                "stream": True,
            },
        },
        "response": {
            "status": 200,
            "headers": {"content-type": "text/event-stream"},
            "events": events,
            "done_seen": False,
            "parse_errors": [],
        },
    }


def _websocket_records(*, input_tokens: int = 8, include_tool: bool = False) -> list[dict[str, Any]]:
    request = {
        "type": "response.create",
        "model": "gpt-5.4",
        "service_tier": "priority",
        "reasoning": {"effort": "high"},
        "tools": [{"type": "function", "name": "lookup"}] if include_tool else [],
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
    }
    payloads: list[tuple[str, dict[str, Any]]] = [
        ("client_to_server", request),
        (
            "server_to_client",
            {"type": "response.created", "response": {"id": "resp_c", "status": "in_progress"}},
        ),
    ]
    if include_tool:
        payloads.append(
            (
                "server_to_client",
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "id": "item_c",
                        "type": "function_call",
                        "call_id": "call_c",
                        "name": "lookup",
                        "arguments": '{"city":"Seoul"}',
                    },
                },
            )
        )
    payloads.append(("server_to_client", _completed("resp_c", input_tokens=input_tokens)))
    return [
        {
            "kind": "websocket_message",
            "transport": "websocket",
            "flow_id": "ws-1",
            "message_index": index,
            "timestamp": float(index),
            "direction": direction,
            "data": data,
            "parse_errors": [],
        }
        for index, (direction, data) in enumerate(payloads, 1)
    ]


def test_parse_sse_accepts_crlf_multiline_data_and_done() -> None:
    body = (
        ": heartbeat\r\n"
        "id: event-1\r\n"
        "event: response.output_text.delta\r\n"
        'data: {"type":"response.output_text.delta",\r\n'
        'data: "delta":"ok"}\r\n\r\n'
        "data: [DONE]\r\n\r\n"
    )

    events = parse_sse(body)

    assert [event.type for event in events] == ["response.output_text.delta", "done"]
    assert events[0].data["delta"] == "ok"
    assert events[0].event_id == "event-1"
    assert events[1].done is True


def test_explicit_done_flag_is_terminal_independent_of_event_name() -> None:
    event = ProtocolEvent(type="message", data="[DONE]", done=True)

    assert event.is_terminal is True


def test_metadata_digest_restores_known_websocket_event_type() -> None:
    event_name = "responsesapi.websocket_timing"
    event = parse_websocket_data(
        {
            "type": {
                "$sha256": hashlib.sha256(event_name.encode()).hexdigest(),
                "$bytes": len(event_name.encode()),
            },
            "timing_metrics": {},
        }
    )

    assert event.type == event_name


def test_http_sse_without_terminal_is_incomplete() -> None:
    record = _http_sse_record()
    record["response"]["events"] = record["response"]["events"][:-1]

    turn = extract_turns([record])[0]

    assert turn.transport == HTTP_SSE
    assert turn.complete is False
    assert turn.incomplete_reason == "missing_terminal_event"


def test_http_json_is_a_separate_transport() -> None:
    record = {
        "kind": "http",
        "request": {"body": {"model": "gpt-5.4", "input": "hi"}},
        "response": {
            "headers": {"content-type": "application/json"},
            "body": {"id": "resp_1", "object": "response", "status": "completed", "output": []},
        },
    }

    turn = extract_turns([record])[0]

    assert turn.transport == HTTP_JSON
    assert turn.event_types == ["http.response"]
    assert turn.complete is True


def test_stale_http_json_label_yields_to_sse_body() -> None:
    record = {
        "kind": "http",
        "transport": "http_json",
        "request": {"body": {"model": "gpt-5.4", "stream": True}},
        "response": {
            "headers": {},
            "body": (
                'event: response.completed\ndata: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
        },
    }

    turn = extract_turns([record])[0]

    assert turn.transport == HTTP_SSE
    assert turn.complete is True


def test_model_discovery_is_not_an_inference_turn() -> None:
    record = {
        "kind": "http",
        "transport": "http_json",
        "request": {
            "method": "GET",
            "url": "https://chatgpt.com/backend-api/codex/models?client_version=0.150.1",
            "body": None,
        },
        "response": {"status": 200, "headers": {"content-type": "application/json"}, "body": {}},
    }

    assert extract_turns([record]) == []


def test_server_observable_profile_keeps_identity_and_tls_results_separate() -> None:
    record_a = _http_sse_record(flow_id="direct")
    record_c = _http_sse_record(flow_id="lb")
    identity = {
        "user-agent": "codex_cli_rs/0.149.0",
        "originator": "codex_cli_rs",
        "version": "0.149.0",
        "accept": "*/*",
    }
    record_a["request"]["headers"] = dict(identity)
    record_c["request"]["headers"] = dict(identity)
    record_a["network"] = {
        "http_version": "HTTP/2.0",
        "tls": {"alpn": "h2", "version": "TLSv1.3", "selected_cipher": "TLS_AES_256_GCM_SHA384"},
    }
    record_c["network"] = {
        "http_version": "HTTP/1.1",
        "tls": {"alpn": None, "version": "TLSv1.3", "selected_cipher": "TLS_AES_256_GCM_SHA384"},
    }
    turn_a = extract_turns([record_a])
    turn_c = extract_turns([record_c])

    result = compare_turns(turn_c, turn_c, turn_a)
    comparison = result["server_observable_a_vs_c"]["turns"][0]["comparison"]

    assert comparison["dimensions"]["identity"]["matches"] is True
    assert comparison["dimensions"]["protocol"]["matches"] is False
    assert comparison["dimensions"]["tls"]["matches"] is False
    assert comparison["dimensions"]["observed_source"]["matches"] is None
    assert comparison["dimensions"]["header_order"]["matches"] is None
    assert comparison["dimensions"]["header_casing"]["matches"] is None
    assert comparison["unobserved_dimensions"] == ["observed_source", "asn", "header_order", "header_casing"]
    assert comparison["all_observed_dimensions_match"] is False
    report = build_report(result)
    assert "## Server-observable A↔C Profile" in report
    assert "| 1 | FAIL | FAIL | PASS | N/A | N/A | PASS | PASS | N/A | N/A |" in report


def test_server_observable_header_sequence_separates_order_and_casing() -> None:
    record_a = _http_sse_record(flow_id="direct")
    record_c = _http_sse_record(flow_id="lb")
    record_a["request"]["header_names"] = ["accept", "content-type", "x-repeat", "x-repeat"]
    record_c["request"]["header_names"] = ["Accept", "Content-Type", "X-Repeat", "X-Repeat"]

    result = compare_turns(extract_turns([record_c]), extract_turns([record_c]), extract_turns([record_a]))
    dimensions = result["server_observable_a_vs_c"]["turns"][0]["comparison"]["dimensions"]

    assert dimensions["header_order"]["matches"] is True
    assert dimensions["header_casing"]["matches"] is False

    record_c["request"]["header_names"] = ["accept", "x-repeat", "content-type"]
    result = compare_turns(extract_turns([record_c]), extract_turns([record_c]), extract_turns([record_a]))
    dimensions = result["server_observable_a_vs_c"]["turns"][0]["comparison"]["dimensions"]

    assert dimensions["header_order"]["matches"] is False
    assert dimensions["header_casing"]["matches"] is False


def test_server_observable_tls_ignores_randomized_extension_order() -> None:
    record_a = _http_sse_record(flow_id="direct")
    record_c = _http_sse_record(flow_id="lb")
    hello = {
        "sni": "chatgpt.com",
        "offered_alpn": ["h2", "http/1.1"],
        "legacy_version": 771,
        "ciphers": [4866, 4865, 4867],
        "extensions": [0, 16, 43, 51],
        "extension_lengths": [
            {"type": 0, "bytes": 16},
            {"type": 16, "bytes": 14},
            {"type": 43, "bytes": 5},
            {"type": 51, "bytes": 1258},
        ],
        "supported_groups": [4588, 29, 23, 24],
        "point_formats": [0],
        "signature_algorithms": [1283, 1027],
        "key_share_groups": [4588, 29],
        "ja3": "raw-a",
        "ja3_md5": "raw-a",
        "client_hello_sha256": "raw-a",
    }
    shuffled = {
        **hello,
        "extensions": [51, 0, 43, 16],
        "extension_lengths": list(reversed(hello["extension_lengths"])),
        "ja3": "raw-c",
        "ja3_md5": "raw-c",
        "client_hello_sha256": "raw-c",
    }
    for record, client_hello in ((record_a, hello), (record_c, shuffled)):
        record["network"] = {
            "http_version": "HTTP/2.0",
            "tls": {
                "alpn": "h2",
                "version": "TLSv1.3",
                "selected_cipher": "TLS_AES_256_GCM_SHA384",
                "client_hello": client_hello,
            },
        }

    result = compare_turns(extract_turns([record_c]), extract_turns([record_c]), extract_turns([record_a]))
    tls = result["server_observable_a_vs_c"]["turns"][0]["comparison"]["dimensions"]["tls"]

    assert tls["matches"] is True
    assert tls["wire_exact_matches"] is False


def test_server_observable_source_requires_same_attested_boundary() -> None:
    record_a = _http_sse_record(flow_id="direct")
    record_c = _http_sse_record(flow_id="lb")
    for record in (record_a, record_c):
        record["network"] = {
            "source_observer": {
                "observer_id_sha256": "shared-observer",
                "role": "intercept",
                "source_host": {"family": "ipv4", "hmac_sha256": "shared-source"},
            }
        }

    result = compare_turns(extract_turns([record_c]), extract_turns([record_c]), extract_turns([record_a]))
    source = result["server_observable_a_vs_c"]["turns"][0]["comparison"]["dimensions"]["observed_source"]

    assert source["matches"] is True
    assert source["claim_scope"] == "intercept_boundary"
    assert source["public_source_ip_evidence"] is False

    record_c["network"]["source_observer"]["observer_id_sha256"] = "other-observer"
    result = compare_turns(extract_turns([record_c]), extract_turns([record_c]), extract_turns([record_a]))
    source = result["server_observable_a_vs_c"]["turns"][0]["comparison"]["dimensions"]["observed_source"]

    assert source["matches"] is None
    assert source["status"] == "unobserved"
    assert source["reason"] == "different_observer_boundary"


def test_controlled_origin_source_mismatch_is_visible() -> None:
    record_a = _http_sse_record(flow_id="direct")
    record_c = _http_sse_record(flow_id="lb")
    record_a["network"] = {
        "source_observer": {
            "observer_id_sha256": "controlled-origin",
            "role": "origin",
            "source_host": {"family": "ipv4", "hmac_sha256": "source-a"},
        }
    }
    record_c["network"] = {
        "source_observer": {
            "observer_id_sha256": "controlled-origin",
            "role": "origin",
            "source_host": {"family": "ipv4", "hmac_sha256": "source-c"},
        }
    }

    result = compare_turns(extract_turns([record_c]), extract_turns([record_c]), extract_turns([record_a]))
    comparison = result["server_observable_a_vs_c"]["turns"][0]["comparison"]
    source = comparison["dimensions"]["observed_source"]

    assert source["matches"] is False
    assert source["claim_scope"] == "controlled_origin"
    assert source["public_source_ip_evidence"] is True
    assert comparison["all_observed_dimensions_match"] is False


def test_controlled_origin_asn_requires_matching_database_provenance() -> None:
    record_a = _http_sse_record(flow_id="direct")
    record_c = _http_sse_record(flow_id="lb")

    def observer(database_sha256: str) -> dict[str, Any]:
        return {
            "observer_id_sha256": "controlled-origin",
            "role": "origin",
            "source_host": {"family": "ipv4", "hmac_sha256": "same-source"},
            "asn": {
                "status": "observed",
                "number": 64500,
                "organization_sha256": "org-digest",
                "database": {"sha256": database_sha256, "build_epoch": 123, "type": "GeoLite2-ASN"},
            },
        }

    record_a["network"] = {"source_observer": observer("same-db")}
    record_c["network"] = {"source_observer": observer("same-db")}
    result = compare_turns(extract_turns([record_c]), extract_turns([record_c]), extract_turns([record_a]))
    asn = result["server_observable_a_vs_c"]["turns"][0]["comparison"]["dimensions"]["asn"]

    assert asn["matches"] is True
    assert asn["claim_scope"] == "controlled_origin"
    assert asn["public_egress_asn_evidence"] is True

    record_c["network"]["source_observer"] = observer("other-db")
    result = compare_turns(extract_turns([record_c]), extract_turns([record_c]), extract_turns([record_a]))
    asn = result["server_observable_a_vs_c"]["turns"][0]["comparison"]["dimensions"]["asn"]

    assert asn["matches"] is None
    assert asn["reason"] == "different_asn_database"


def _tls_records(transport: str, orders: Sequence[tuple[int, ...]], *, cohort: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, order in enumerate(orders):
        hello = {
            "sni": "origin.example",
            "offered_alpn": ["h2", "http/1.1"],
            "legacy_version": 771,
            "ciphers": [4866, 4865, 4867],
            "extensions": list(order),
            "extension_lengths": [{"type": extension, "bytes": extension + 1} for extension in order],
            "supported_groups": [4588, 29, 23, 24],
            "point_formats": [0],
            "signature_algorithms": [2055, 1027],
            "key_share_groups": [4588, 29],
            "ja3_md5": f"ja3-{cohort}-{index}",
            "client_hello_sha256": f"hello-{cohort}-{index}",
        }
        records.append(
            {
                "kind": "websocket_message" if transport == "websocket" else "http",
                "transport": transport,
                "network": {
                    "tls": {
                        "alpn": "h2",
                        "version": "TLSv1.3",
                        "selected_cipher": "TLS_AES_256_GCM_SHA384",
                        "client_hello": hello,
                    }
                },
            }
        )
    return records


def test_tls_randomization_accepts_direct_like_distribution_and_rejects_fixed_order() -> None:
    permutations = list(itertools.permutations((0, 10, 16, 43)))
    reference_orders = permutations * 2
    direct_orders = permutations[7:] + permutations[:7] + permutations
    candidate_orders = permutations[13:] + permutations[:13] + permutations
    reference = _tls_records("websocket", reference_orders, cohort="reference")
    direct = _tls_records("websocket", direct_orders, cohort="direct")
    candidate = _tls_records("websocket", candidate_orders, cohort="candidate")

    matched = tls_randomization.analyze_tls_randomization_records(reference, direct, candidate)
    websocket = matched["transports"]["websocket"]

    assert websocket["matches"] is True
    assert websocket["stable_profiles_match"] is True
    assert websocket["candidate_a_vs_c_distance"] <= websocket["acceptance_limit"]
    assert websocket["cohorts"]["path_c"]["pairwise_order_entropy"] > 0.99

    fixed = _tls_records("websocket", [(0, 10, 16, 43)] * 48, cohort="fixed")
    mismatched = tls_randomization.analyze_tls_randomization_records(reference, direct, fixed)
    websocket = mismatched["transports"]["websocket"]

    assert websocket["matches"] is False
    assert websocket["stable_profiles_match"] is True
    assert websocket["extension_order_matches_direct_variance"] is False
    assert websocket["cohorts"]["path_c"]["pairwise_order_entropy"] == 0.0


def test_tls_randomization_deduplicates_frames_and_never_passes_too_few_samples() -> None:
    records = _tls_records("websocket", [(0, 10, 16, 43)], cohort="one-connection")
    records *= 30

    result = tls_randomization.analyze_tls_randomization_records(records, records, records)
    websocket = result["transports"]["websocket"]

    assert websocket["matches"] is None
    assert websocket["reason"] == "insufficient_independent_client_hellos"
    assert websocket["cohorts"]["path_c"]["samples"] == 1


def test_http_json_none_mode_uses_status_for_terminal_class() -> None:
    record = {
        "kind": "http",
        "transport": "http_json",
        "capture_body_mode": "none",
        "request": {"body": None},
        "response": {"status": 200, "headers": {"content-type": "application/json"}, "body": None},
    }
    turns = extract_turns([record])

    result = compare_turns(turns, turns)

    assert result["summary"]["overall_pass"] is False
    assert result["path_b_vs_c"]["turns"][0]["checks"]["terminal_class"] is True
    assert "insufficient_capture_body" in {
        mismatch["category"] for mismatch in result["path_b_vs_c"]["hard_mismatches"]
    }


def test_websocket_reconstructs_multiple_turns_and_reports_orphans() -> None:
    records = _websocket_records()
    second = _websocket_records(input_tokens=9)
    for index, record in enumerate(second, len(records) + 1):
        record["message_index"] = index
        record["flow_id"] = "ws-1"
    records.extend(second)
    records.append(
        {
            "kind": "websocket_message",
            "flow_id": "ws-2",
            "message_index": 1,
            "direction": "server_to_client",
            "data": {"type": "error", "code": "handshake_failed"},
            "parse_errors": [],
        }
    )

    extraction = extract_turns_with_diagnostics(records)

    assert len(extraction.turns) == 2
    assert [turn.terminal_event for turn in extraction.turns] == ["response.completed", "response.completed"]
    assert all(turn.complete for turn in extraction.turns)
    assert len(extraction.orphan_websocket_messages) == 1
    assert extraction.orphan_websocket_messages[0].reason == "server_message_without_active_turn"


def test_websocket_multiplexing_correlates_reverse_terminal_order() -> None:
    first_id = {"$sha256": "a" * 64, "$bytes": 10}
    second_id = {"$sha256": "b" * 64, "$bytes": 11}
    payloads = [
        ("client_to_server", {"type": "response.create", "model": "gpt-5.4", "input": "first"}),
        (
            "server_to_client",
            {"type": "response.created", "response": {"id": first_id, "status": "in_progress"}},
        ),
        ("client_to_server", {"type": "response.create", "model": "gpt-5.5", "input": "second"}),
        (
            "server_to_client",
            {"type": "response.created", "response": {"id": second_id, "status": "in_progress"}},
        ),
        ("server_to_client", _completed(second_id, input_tokens=2)),
        ("server_to_client", _completed(first_id, input_tokens=1)),
    ]
    records = [
        {
            "kind": "websocket_message",
            "flow_id": "multiplexed",
            "message_index": index,
            "direction": direction,
            "data": data,
            "parse_errors": [],
        }
        for index, (direction, data) in enumerate(payloads, 1)
    ]

    extraction = extract_turns_with_diagnostics(records)

    assert extraction.orphan_websocket_messages == []
    assert [turn.request["model"] for turn in extraction.turns] == ["gpt-5.4", "gpt-5.5"]
    assert [turn.terminal.data["response"]["id"] for turn in extraction.turns if turn.terminal is not None] == [
        first_id,
        second_id,
    ]
    assert all(turn.complete for turn in extraction.turns)


def test_websocket_multiplexing_routes_idless_and_item_scoped_events() -> None:
    payloads = [
        ("client_to_server", {"type": "response.create", "model": "first", "input": "one"}),
        ("client_to_server", {"type": "response.create", "model": "second", "input": "two"}),
        (
            "server_to_client",
            {"type": "response.created", "response": {"id": "resp_1", "status": "in_progress"}},
        ),
        (
            "server_to_client",
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"id": "item_1", "type": "message", "status": "in_progress"},
            },
        ),
        (
            "server_to_client",
            {"type": "response.created", "response": {"id": "resp_2", "status": "in_progress"}},
        ),
        (
            "server_to_client",
            {"type": "response.output_text.delta", "item_id": "item_1", "delta": "first"},
        ),
        ("server_to_client", _completed("resp_2", input_tokens=2)),
        ("server_to_client", _completed("resp_1", input_tokens=1)),
    ]
    records = [
        {
            "kind": "websocket_message",
            "flow_id": "multiplexed-items",
            "message_index": index,
            "direction": direction,
            "data": data,
            "parse_errors": [],
        }
        for index, (direction, data) in enumerate(payloads, 1)
    ]

    extraction = extract_turns_with_diagnostics(records)

    assert extraction.orphan_websocket_messages == []
    assert [event.type for event in extraction.turns[0].events] == [
        "response.created",
        "response.output_item.added",
        "response.output_text.delta",
        "response.completed",
    ]
    assert [event.type for event in extraction.turns[1].events] == [
        "response.created",
        "response.completed",
    ]


def test_sse_to_websocket_same_run_semantics_pass() -> None:
    record_b = _http_sse_record(include_tool=True)
    record_b["response"]["events"][-1]["data"]["response"]["output"] = [
        dict(record_b["response"]["events"][1]["data"]["item"])
    ]
    turn_b = extract_turns([record_b])
    turn_c = extract_turns(_websocket_records(include_tool=True))

    result = compare_turns(turn_b, turn_c)

    assert result["summary"]["overall_pass"] is True
    row = result["path_b_vs_c"]["turns"][0]
    assert row["checks"]["transport"] == {"same": False, "translated": True, "supported": True}
    assert row["checks"]["material_events"]["payloads_match"] is True


def test_transport_timing_derived_cache_key_and_null_defaults_are_non_material() -> None:
    record_b = _http_sse_record()
    record_b["response"]["events"].insert(
        -1,
        {
            "event": "responsesapi.websocket_timing",
            "data": {"type": "responsesapi.websocket_timing", "timing_metrics": {"ttft_ms": 12}},
        },
    )
    records_c = _websocket_records()
    records_c[0]["data"]["prompt_cache_key"] = "derived-by-lb"
    records_c[-1]["data"]["response"]["error"] = None
    records_c[-1]["data"]["response"]["incomplete_details"] = None
    records_c.insert(
        -1,
        {
            "kind": "websocket_message",
            "transport": "websocket",
            "flow_id": "ws-1",
            "message_index": records_c[-1]["message_index"],
            "timestamp": float(records_c[-1]["message_index"]),
            "direction": "server_to_client",
            "data": {"type": "responsesapi.websocket_timing", "timing_metrics": {"ttft_ms": 12}},
            "parse_errors": [],
        },
    )
    records_c[-1]["message_index"] += 1
    records_c[-1]["timestamp"] += 1

    result = compare_turns(extract_turns([record_b]), extract_turns(records_c))

    assert result["summary"]["overall_pass"] is True
    row = result["path_b_vs_c"]["turns"][0]
    assert row["checks"]["semantic_request"] is True
    assert row["checks"]["response_semantics"] is True
    assert row["checks"]["material_events"]["payloads_match"] is True


def test_usage_mismatch_fails_same_run_gate() -> None:
    turn_b = extract_turns([_http_sse_record(input_tokens=8)])
    turn_c = extract_turns(_websocket_records(input_tokens=9))

    result = compare_turns(turn_b, turn_c)

    assert result["summary"]["overall_pass"] is False
    assert "usage" in {item["category"] for item in result["path_b_vs_c"]["hard_mismatches"]}


def test_request_content_loss_fails_same_run_gate() -> None:
    turn_b = extract_turns([_http_sse_record()])
    records_c = _websocket_records()
    records_c[0]["data"]["input"][0]["content"][0]["text"] = "different"
    turn_c = extract_turns(records_c)

    result = compare_turns(turn_b, turn_c)

    assert result["summary"]["overall_pass"] is False
    assert "semantic_request" in {item["category"] for item in result["path_b_vs_c"]["hard_mismatches"]}


def test_request_tool_history_identity_and_type_are_material() -> None:
    history_b = [
        {
            "type": "function_call",
            "id": "item_1",
            "call_id": "call_A",
            "status": "completed",
            "name": "lookup",
            "arguments": '{"city":"Seoul"}',
        },
        {"type": "function_call_output", "call_id": "call_A", "output": "sunny"},
    ]
    history_c = json.loads(json.dumps(history_b))
    record_b = _http_sse_record()
    records_c = _websocket_records()
    record_b["request"]["body"]["input"] = history_b
    records_c[0]["data"]["input"] = history_c

    same = compare_turns(extract_turns([record_b]), extract_turns(records_c))
    assert same["summary"]["overall_pass"] is True

    history_c[0]["name"] = "danger"
    history_c[0]["call_id"] = "call_B"
    history_c[1]["call_id"] = "call_B"
    changed = compare_turns(extract_turns([record_b]), extract_turns(records_c))

    assert changed["summary"]["overall_pass"] is False
    assert "semantic_request" in {item["category"] for item in changed["path_b_vs_c"]["hard_mismatches"]}


def test_previous_response_id_is_exact_in_same_run_comparison() -> None:
    record_b = _http_sse_record()
    records_c = _websocket_records()
    record_b["request"]["body"]["previous_response_id"] = "resp_parent"
    records_c[0]["data"]["previous_response_id"] = "resp_parent"

    same = compare_turns(extract_turns([record_b]), extract_turns(records_c))
    assert same["summary"]["overall_pass"] is True

    records_c[0]["data"]["previous_response_id"] = "resp_other"
    changed = compare_turns(extract_turns([record_b]), extract_turns(records_c))

    assert changed["summary"]["overall_pass"] is False
    assert "semantic_request" in {item["category"] for item in changed["path_b_vs_c"]["hard_mismatches"]}


def test_public_v1_adapter_defaults_and_messages_are_canonicalized() -> None:
    record_b = _http_sse_record()
    records_c = _websocket_records()
    body_b = record_b["request"]["body"]
    body_b.pop("input")
    body_b.pop("stream")
    body_b["messages"] = [{"role": "user", "content": "hello"}]
    records_c[0]["data"]["instructions"] = ""
    records_c[0]["data"]["include"] = []
    records_c[0]["data"]["store"] = False

    result = compare_turns(extract_turns([record_b]), extract_turns(records_c))

    assert result["summary"]["overall_pass"] is True


def test_public_v1_tool_messages_match_canonical_upstream_items() -> None:
    record_b = _http_sse_record()
    records_c = _websocket_records()
    body_b = record_b["request"]["body"]
    body_b["messages"] = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_A",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"city":"Seoul"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_A", "content": "sunny"},
    ]
    body_b.pop("input")
    records_c[0]["data"]["input"] = [
        {
            "type": "function_call",
            "call_id": "call_A",
            "name": "lookup",
            "arguments": '{"city":"Seoul"}',
        },
        {"type": "function_call_output", "call_id": "call_A", "output": "sunny"},
    ]
    records_c[0]["data"]["instructions"] = ""
    records_c[0]["data"]["include"] = []

    result = compare_turns(extract_turns([record_b]), extract_turns(records_c))

    assert result["summary"]["overall_pass"] is True


def test_public_v1_multimodal_message_matches_canonical_input() -> None:
    record_b = _http_sse_record()
    records_c = _websocket_records()
    body_b = record_b["request"]["body"]
    body_b.pop("input")
    body_b["messages"] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.test/image.png", "detail": "high"},
                }
            ],
        }
    ]
    records_c[0]["data"]["input"] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": "https://example.test/image.png",
                    "detail": "high",
                }
            ],
        }
    ]
    records_c[0]["data"]["instructions"] = ""
    records_c[0]["data"]["include"] = []

    result = compare_turns(extract_turns([record_b]), extract_turns(records_c))

    assert result["summary"]["overall_pass"] is True


def test_public_v1_assistant_refusal_matches_canonical_output_text() -> None:
    record_b = _http_sse_record()
    records_c = _websocket_records()
    body_b = record_b["request"]["body"]
    body_b.pop("input")
    body_b["messages"] = [{"role": "assistant", "content": None, "refusal": "cannot comply"}]
    records_c[0]["data"]["input"] = [
        {"role": "assistant", "content": [{"type": "output_text", "text": "cannot comply"}]}
    ]
    records_c[0]["data"]["instructions"] = ""
    records_c[0]["data"]["include"] = []

    result = compare_turns(extract_turns([record_b]), extract_turns(records_c))

    assert result["summary"]["overall_pass"] is True


def test_tool_output_parts_match_native_string_normalization() -> None:
    record_b = _http_sse_record()
    records_c = _websocket_records()
    record_b["request"]["body"]["input"] = [
        {
            "role": "tool",
            "tool_call_id": "call_A",
            "content": [
                {"type": "output_text", "text": "sun"},
                {"type": "image_url", "image_url": "ignored"},
                {"type": "refusal", "refusal": "ny"},
            ],
        }
    ]
    records_c[0]["data"]["input"] = [{"type": "function_call_output", "call_id": "call_A", "output": "sunny"}]

    result = compare_turns(extract_turns([record_b]), extract_turns(records_c))

    assert result["summary"]["overall_pass"] is True


def test_metadata_semantic_projection_handles_joined_instructions(monkeypatch: Any) -> None:
    addon = _load_addon(monkeypatch)
    record_b = _http_sse_record()
    records_c = _websocket_records()
    body_b = record_b["request"]["body"]
    body_b.pop("input")
    body_b["messages"] = [
        {"role": "system", "content": "one"},
        {"role": "developer", "content": "two"},
        {"role": "user", "content": "hello"},
    ]
    body_c = records_c[0]["data"]
    body_c["instructions"] = "one\ntwo"
    body_c["include"] = []
    record_b["request"]["semantic_body"] = addon._capture_request_semantics(json.dumps(body_b).encode(), "metadata")
    records_c[0]["semantic_data"] = addon._capture_request_semantics(json.dumps(body_c).encode(), "metadata")

    result = compare_turns(extract_turns([record_b]), extract_turns(records_c))

    assert result["summary"]["overall_pass"] is True


def test_conditional_request_value_change_fails_but_expected_omission_is_observed() -> None:
    record_b = _http_sse_record()
    record_b["request"]["body"]["max_output_tokens"] = 100
    records_c = _websocket_records()
    records_c[0]["data"]["max_output_tokens"] = 1

    changed = compare_turns(extract_turns([record_b]), extract_turns(records_c))

    assert "conditional_request_field" in {item["category"] for item in changed["path_b_vs_c"]["hard_mismatches"]}

    del records_c[0]["data"]["max_output_tokens"]
    omitted = compare_turns(extract_turns([record_b]), extract_turns(records_c))
    conditional = omitted["path_b_vs_c"]["turns"][0]["checks"]["conditional_request_fields"]
    assert conditional["path_b_only"] == ["max_output_tokens"]
    assert "conditional_request_field" not in {item["category"] for item in omitted["path_b_vs_c"]["hard_mismatches"]}


def test_direct_baseline_differences_are_informational() -> None:
    turn_b = extract_turns([_http_sse_record(input_tokens=8)])
    turn_c = extract_turns(_websocket_records(input_tokens=8))
    turn_a = extract_turns([_http_sse_record(input_tokens=99)])

    result = compare_turns(turn_b, turn_c, turn_a)

    assert result["summary"]["overall_pass"] is True
    assert result["path_a_baseline"]["turns"][0]["protocol_observations"]["usage_delta"] is not None


def test_compare_paths_fails_closed_on_bad_capture_and_orphan_server_frame(tmp_path: Path) -> None:
    path_b = tmp_path / "b.jsonl"
    path_c = tmp_path / "c.jsonl"
    path_b.write_text("not-json\n", encoding="utf-8")
    orphan = {
        "kind": "websocket_message",
        "flow_id": "ws-c",
        "message_index": 1,
        "direction": "server_to_client",
        "data": {"type": "error", "code": "orphan"},
        "parse_errors": [],
    }
    path_c.write_text(json.dumps(orphan) + "\n", encoding="utf-8")

    result = compare_paths(str(path_b), str(path_c))

    assert result["summary"]["overall_pass"] is False
    categories = {item["category"] for item in result["path_b_vs_c"]["hard_mismatches"]}
    assert "capture_error" in categories
    assert "orphan_websocket_response" in categories


def test_compare_paths_integrates_tls_reference_and_markdown_report(tmp_path: Path) -> None:
    orders = [(0, 10, 16, 43), (43, 16, 10, 0)]

    def inference_records(cohort: str) -> list[dict[str, Any]]:
        tls_records = _tls_records("http_sse", orders, cohort=cohort)
        records = [_http_sse_record(flow_id=f"{cohort}-{index}") for index in range(2)]
        for record, tls_record in zip(records, tls_records, strict=True):
            record["network"] = tls_record["network"]
        return records

    paths = {name: tmp_path / f"{name}.jsonl" for name in ("a_reference", "a", "b", "c")}
    payloads = {
        "a_reference": _tls_records("http_sse", list(reversed(orders)), cohort="reference"),
        "a": inference_records("a"),
        "b": inference_records("b"),
        "c": inference_records("c"),
    }
    for name, path in paths.items():
        path.write_text("".join(json.dumps(record) + "\n" for record in payloads[name]), encoding="utf-8")

    result = compare_paths(
        str(paths["b"]),
        str(paths["c"]),
        str(paths["a"]),
        path_a_reference=str(paths["a_reference"]),
        tls_min_samples=2,
    )

    assert result["summary"]["overall_pass"] is True
    assert result["tls_randomization_a_vs_c"]["available"] is True
    assert result["tls_randomization_a_vs_c"]["transports"]["http_sse"]["matches"] is True
    report = build_report(result)
    assert "## TLS Extension-order Randomization" in report
    assert "| http_sse | 2 | 2 | 2 | PASS |" in report


def test_report_is_derived_from_comparison() -> None:
    result = compare_turns(
        extract_turns([_http_sse_record()]),
        extract_turns(_websocket_records(input_tokens=9)),
    )

    report = build_report(result)

    assert "**FAIL**" in report
    assert "usage" in report
    assert "http_sse" in report
    assert "websocket" in report


def test_cli_strict_exit_and_report_artifacts(tmp_path: Path, capsys: Any) -> None:
    path_b = tmp_path / "b.jsonl"
    path_c = tmp_path / "c.jsonl"
    json_output = tmp_path / "result.json"
    markdown_output = tmp_path / "report.md"
    path_b.write_text(json.dumps(_http_sse_record(input_tokens=8)) + "\n", encoding="utf-8")
    path_c.write_text(
        "".join(json.dumps(record) + "\n" for record in _websocket_records(input_tokens=9)),
        encoding="utf-8",
    )

    compare_exit = compare_module.main(
        [
            "--path-b",
            str(path_b),
            "--path-c",
            str(path_c),
            "--json-output",
            str(json_output),
            "--strict",
        ]
    )
    report_exit = report_module.main(
        [
            "--path-b",
            str(path_b),
            "--path-c",
            str(path_c),
            "--output",
            str(markdown_output),
            "--strict",
        ]
    )

    assert compare_exit == 2
    assert report_exit == 2
    assert json.loads(json_output.read_text(encoding="utf-8"))["summary"]["overall_pass"] is False
    assert "**FAIL**" in markdown_output.read_text(encoding="utf-8")
    assert "Report written:" in capsys.readouterr().out


def _load_addon(monkeypatch: Any) -> Any:
    fake_mitmproxy = types.ModuleType("mitmproxy")
    fake_http = types.ModuleType("mitmproxy.http")
    setattr(fake_http, "HTTPFlow", object)
    setattr(fake_mitmproxy, "http", fake_http)
    setattr(
        fake_mitmproxy,
        "ctx",
        SimpleNamespace(
            options=SimpleNamespace(
                capture_body_mode="metadata",
                capture_output="/tmp/unused-codex-traffic.jsonl",
                capture_observer_id="",
                capture_observer_role="intercept",
                capture_source_hmac_key_file="",
                capture_asn_mmdb="",
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "mitmproxy", fake_mitmproxy)
    monkeypatch.setitem(sys.modules, "mitmproxy.http", fake_http)
    sys.modules.pop("scripts.traffic_analysis.mitmproxy_addon", None)
    return importlib.import_module("scripts.traffic_analysis.mitmproxy_addon")


def test_metadata_capture_hashes_content_and_always_redacts_credentials(monkeypatch: Any) -> None:
    addon = _load_addon(monkeypatch)

    body = addon._sanitize_metadata(
        {
            "type": "response.create",
            "model": "gpt-5.4",
            "input": "private prompt",
            "tools": [{"type": "function", "name": "lookup", "description": "private details"}],
        }
    )
    headers = addon._redact_headers(
        {
            "Authorization": "Bearer secret",
            "x-api-key": "sk-secret",
            "Cookie": "session=secret",
            "content-type": "application/json",
        }
    )

    assert body["type"] == "response.create"
    assert body["model"]["$bytes"] == len("gpt-5.4")
    assert body["input"]["$bytes"] == len("private prompt")
    assert "private prompt" not in json.dumps(body)
    assert set(headers.values()) == {"[REDACTED]", "application/json"}
    assert "secret" not in json.dumps(headers)
    redacted_url = addon._redact_url("https://user:secret@example.test/v1/responses?access_token=secret&trace=1")
    assert "secret" not in redacted_url
    assert "trace=1" in redacted_url


def test_metadata_capture_hashes_arbitrary_structural_and_usage_strings(monkeypatch: Any) -> None:
    addon = _load_addon(monkeypatch)
    secrets = ["patient-secret", "customer-secret", "custom-type-secret"]

    body = addon._sanitize_metadata(
        {
            "metadata": {"usage": {"note": secrets[0]}, "name": secrets[1]},
            "extension": {"type": secrets[2]},
        }
    )

    serialized = json.dumps(body)
    assert all(secret not in serialized for secret in secrets)
    assert body["metadata"]["usage"]["note"]["$bytes"] == len(secrets[0])


def test_metadata_capture_preserves_responsesapi_event_names(monkeypatch: Any) -> None:
    addon = _load_addon(monkeypatch)

    body = addon._sanitize_metadata(
        {"type": "responsesapi.websocket_timing", "timing_metrics": {"private_note": "secret"}}
    )

    assert body["type"] == "responsesapi.websocket_timing"
    assert body["timing_metrics"]["private_note"]["$bytes"] == len("secret")


def test_metadata_capture_hashes_codex_identity_headers(monkeypatch: Any) -> None:
    addon = _load_addon(monkeypatch)
    secret = '{"workspace":"/private/repo","session_id":"session-secret"}'

    headers = addon._redact_headers(
        {
            "chatgpt-account-id": "account-secret",
            "x-codex-installation-id": "installation-secret",
            "x-codex-turn-metadata": secret,
            "session-id": "session-secret",
            "user-agent": "codex/1",
        },
        metadata=True,
    )

    assert secret not in json.dumps(headers)
    assert "session-secret" not in json.dumps(headers)
    assert headers["x-codex-turn-metadata"].startswith("[SHA256:")
    assert headers["chatgpt-account-id"].startswith("[SHA256:")
    assert headers["x-codex-installation-id"].startswith("[SHA256:")
    assert headers["user-agent"] == "codex/1"


def test_header_name_sequence_preserves_order_duplicates_and_casing_without_values(monkeypatch: Any) -> None:
    addon = _load_addon(monkeypatch)

    class FakeHeaders:
        fields = (
            (b"Accept", b"secret-one"),
            (b"X-Repeat", b"secret-two"),
            (b"x-repeat", b"secret-three"),
        )

    sequence = addon._header_name_sequence(FakeHeaders())

    assert sequence == ["Accept", "X-Repeat", "x-repeat"]
    assert "secret" not in json.dumps(sequence)


def test_client_hello_capture_records_wire_fingerprint_without_raw_bytes(monkeypatch: Any) -> None:
    addon = _load_addon(monkeypatch)

    class FakeClientHello:
        cipher_suites = [4866, 4865]
        sni = "chatgpt.com"
        alpn_protocols = [b"h2", b"http/1.1"]
        extensions = [
            (16, b"\x00\x0c\x02h2\x08http/1.1"),
            (10, b"\x00\x04\x00\x1d\x00\x17"),
            (11, b"\x01\x00"),
            (13, b"\x00\x04\x08\x07\x04\x03"),
            (51, b"\x00\x05\x00\x1d\x00\x01x"),
        ]

        @staticmethod
        def raw_bytes(*, wrap_in_record: bool) -> bytes:
            return b"record" if wrap_in_record else b"\x03\x03hello"

    captured = addon._client_hello_observation(FakeClientHello())

    assert captured["sni"] == "chatgpt.com"
    assert captured["offered_alpn"] == ["h2", "http/1.1"]
    assert captured["legacy_version"] == 771
    assert captured["supported_groups"] == [29, 23]
    assert captured["point_formats"] == [0]
    assert captured["signature_algorithms"] == [2055, 1027]
    assert captured["key_share_groups"] == [29]
    assert captured["ja3"] == "771,4866-4865,16-10-11-13-51,29-23,0"
    assert "raw" not in captured
    assert not any(isinstance(value, bytes) for value in captured.values())


def test_client_hello_cache_uses_stable_connection_id(monkeypatch: Any) -> None:
    module = _load_addon(monkeypatch)
    addon = module.CaptureAddon()
    hello = SimpleNamespace(
        cipher_suites=[],
        sni=None,
        alpn_protocols=[],
        extensions=[],
        raw_bytes=lambda **_kwargs: b"\x03\x03",
    )
    addon.tls_clienthello(
        SimpleNamespace(context=SimpleNamespace(client=SimpleNamespace(id="conn-1")), client_hello=hello)
    )
    flow = SimpleNamespace(client_conn=SimpleNamespace(id="conn-1", peername=None))

    observation = addon._network_observation(flow)

    assert "conn-1" in addon._client_hellos
    assert "client_hello" in observation["tls"]


def test_source_observer_hashes_peer_and_observer_id(monkeypatch: Any) -> None:
    addon = _load_addon(monkeypatch)
    addon.ctx.options.capture_observer_id = "same-controlled-observer"
    addon.ctx.options.capture_observer_role = "origin"
    source_hmac_key = b"source-observer-test-key-32-bytes"
    flow = SimpleNamespace(
        client_conn=SimpleNamespace(peername=("203.0.113.9", 43123)),
        request=SimpleNamespace(
            headers={
                "forwarded": "for=198.51.100.7",
                "x-forwarded-for": "198.51.100.8",
            }
        ),
    )

    observed = addon._source_observer(flow, source_hmac_key=source_hmac_key)

    assert observed == {
        "observer_id_sha256": hashlib.sha256(b"same-controlled-observer").hexdigest(),
        "role": "origin",
        "source_host": {
            "family": "ipv4",
            "hmac_sha256": hmac.new(source_hmac_key, b"203.0.113.9", hashlib.sha256).hexdigest(),
        },
    }
    assert "203.0.113.9" not in json.dumps(observed)
    assert hashlib.sha256(b"198.51.100.7").hexdigest() not in json.dumps(observed)
    assert hashlib.sha256(b"198.51.100.8").hexdigest() not in json.dumps(observed)


def test_offline_asn_resolver_retains_only_hashed_org_and_database_provenance(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    addon = _load_addon(monkeypatch)
    database_path = tmp_path / "GeoLite2-ASN.mmdb"
    database_path.write_bytes(b"test-mmdb-provenance")

    class FakeReader:
        closed = False

        @staticmethod
        def metadata() -> Any:
            return SimpleNamespace(build_epoch=1_788_000_000, database_type="GeoLite2-ASN")

        @staticmethod
        def get(address: str) -> dict[str, Any]:
            assert address == "203.0.113.9"
            return {
                "autonomous_system_number": 64500,
                "autonomous_system_organization": "Private Network Operator",
            }

        def close(self) -> None:
            self.closed = True

    reader = FakeReader()
    resolver = addon.OfflineAsnResolver(database_path, reader_factory=lambda _: reader)
    evidence = resolver.observe("203.0.113.9")
    serialized = json.dumps(evidence)

    assert evidence == {
        "status": "observed",
        "number": 64500,
        "organization_sha256": hashlib.sha256(b"Private Network Operator").hexdigest(),
        "database": {
            "sha256": hashlib.sha256(b"test-mmdb-provenance").hexdigest(),
            "build_epoch": 1_788_000_000,
            "type": "GeoLite2-ASN",
        },
    }
    assert "203.0.113.9" not in serialized
    assert "Private Network Operator" not in serialized
    resolver.close()
    assert reader.closed is True


def test_hashed_unknown_type_is_not_treated_as_a_tool_call(monkeypatch: Any) -> None:
    addon = _load_addon(monkeypatch)
    record = _http_sse_record()
    record["response"]["events"].insert(
        -1,
        {
            "event": "response.output_item.done",
            "data": addon._sanitize_metadata(
                {
                    "type": "response.output_item.done",
                    "item": {"type": "future_private_item", "name": "not-a-tool"},
                }
            ),
        },
    )
    turns = extract_turns([record])

    result = compare_turns(turns, turns)

    assert result["summary"]["overall_pass"] is True
    assert result["path_b_vs_c"]["turns"][0]["path_b"]["tool_calls"] == []


def test_addon_classifies_http_sse_and_websocket_separately(monkeypatch: Any) -> None:
    module = _load_addon(monkeypatch)
    captured: list[dict[str, Any]] = []
    addon = module.CaptureAddon()
    monkeypatch.setattr(addon, "_write", captured.append)

    request = SimpleNamespace(
        path="/v1/responses?trace=1",
        content=b'{"model":"gpt-5.4","input":"secret"}',
        headers={"authorization": "Bearer secret", "content-type": "application/json"},
        timestamp_start=1.0,
        method="POST",
        pretty_url="http://lb.test/v1/responses?trace=1",
    )
    response = SimpleNamespace(
        content=(
            b'event: response.completed\ndata: {"type":"response.completed","response":{"status":"completed"}}\n\n'
        ),
        headers={"content-type": "text/event-stream"},
        timestamp_end=2.0,
        status_code=200,
    )
    flow = SimpleNamespace(id="http-flow", request=request, response=response, websocket=None)

    addon.response(flow)

    flow.error = SimpleNamespace(msg="late upstream failure that must not create a duplicate")
    addon.error(flow)

    assert len(captured) == 1
    assert captured[0]["transport"] == "http_sse"
    assert captured[0]["capture_body_mode"] == "metadata"
    assert captured[0]["response"]["events"][0]["event"] == "response.completed"
    assert captured[0]["network"] == {
        "http_version": None,
        "source_observer": {
            "observer_id_sha256": None,
            "role": None,
            "source_host": None,
        },
        "tls": {"alpn": None, "version": None, "selected_cipher": None},
    }
    assert captured[0]["request"]["header_names"] == ["authorization", "content-type"]
    assert "secret" not in json.dumps(captured[0])

    message = SimpleNamespace(
        content='{"type":"response.create","model":"gpt-5.4","input":"secret"}',
        from_client=True,
        timestamp=3.0,
    )
    flow.websocket = SimpleNamespace(messages=[message])
    flow.response.status_code = 101

    addon.websocket_message(flow)

    assert captured[1]["transport"] == "websocket"
    assert captured[1]["capture_body_mode"] == "metadata"
    assert captured[1]["direction"] == "client_to_server"
    assert captured[1]["message_index"] == 1
    assert captured[1]["request"]["method"] == "POST"
    assert captured[1]["request"]["header_names"] == ["authorization", "content-type"]
    assert "secret" not in json.dumps(captured[1])


def test_addon_infers_sse_when_upstream_omits_content_type(monkeypatch: Any) -> None:
    module = _load_addon(monkeypatch)
    captured: list[dict[str, Any]] = []
    addon = module.CaptureAddon()
    monkeypatch.setattr(addon, "_write", captured.append)
    request = SimpleNamespace(
        path="/backend-api/codex/responses",
        content=b'{"model":"gpt-5.6-luna","stream":true}',
        headers={"accept": "text/event-stream"},
        timestamp_start=1.0,
        method="POST",
        pretty_url="https://chatgpt.com/backend-api/codex/responses",
    )
    response = SimpleNamespace(
        content=b'event: response.completed\ndata: {"type":"response.completed","response":{"status":"completed"}}\n\n',
        headers={},
        timestamp_end=2.0,
        status_code=200,
    )

    addon.response(SimpleNamespace(id="no-content-type", request=request, response=response, websocket=None))

    assert captured[0]["transport"] == "http_sse"
    assert captured[0]["response"]["events"][0]["event"] == "response.completed"


def test_addon_keeps_json_error_with_sse_accept_as_http_json(monkeypatch: Any) -> None:
    module = _load_addon(monkeypatch)
    captured: list[dict[str, Any]] = []
    addon = module.CaptureAddon()
    monkeypatch.setattr(addon, "_write", captured.append)
    request = SimpleNamespace(
        path="/backend-api/codex/responses",
        content=b'{"model":"gpt-5.3-codex-spark","stream":true}',
        headers={"accept": "text/event-stream"},
        timestamp_start=1.0,
        method="POST",
        pretty_url="https://chatgpt.com/backend-api/codex/responses",
    )
    response = SimpleNamespace(
        content=b'{"detail":"usage limit"}',
        headers={"content-type": "application/json"},
        timestamp_end=2.0,
        status_code=429,
    )

    addon.response(SimpleNamespace(id="json-error", request=request, response=response, websocket=None))

    assert captured[0]["transport"] == "http_json"


def test_addon_captures_http_transport_error_without_raw_message_or_duplicate(monkeypatch: Any) -> None:
    module = _load_addon(monkeypatch)
    captured: list[dict[str, Any]] = []
    addon = module.CaptureAddon()
    monkeypatch.setattr(addon, "_write", captured.append)
    request = SimpleNamespace(
        path="/backend-api/codex/responses",
        content=b'{"model":"gpt-5.6-luna","input":"private","stream":true}',
        headers={"authorization": "Bearer secret", "content-type": "application/json"},
        timestamp_start=1.0,
        method="POST",
        pretty_url="https://chatgpt.com/backend-api/codex/responses",
        http_version="HTTP/2.0",
    )
    flow = SimpleNamespace(
        id="timeout-flow",
        request=request,
        response=None,
        websocket=None,
        client_conn=None,
        error=SimpleNamespace(msg="upstream timed out at 203.0.113.99 with secret-proxy"),
    )

    addon.error(flow)
    addon.error(flow)

    assert len(captured) == 1
    record = captured[0]
    assert record["transport"] == "http_sse"
    assert record["response"]["network_error"] == {"category": "timeout"}
    assert record["request"]["headers"]["authorization"] == "[REDACTED]"
    serialized = json.dumps(record)
    assert "203.0.113.99" not in serialized
    assert "secret-proxy" not in serialized
    assert "Bearer secret" not in serialized

    turn = extract_turns([record])[0]
    assert turn.complete is False
    assert turn.incomplete_reason == "network_error"


def test_failure_outcomes_are_reported_without_weakening_strict_parity() -> None:
    record = _http_sse_record()
    record["response"]["events"] = record["response"]["events"][:1]
    record["response"]["network_error"] = {"category": "connection_closed"}
    turns = extract_turns([record])

    result = compare_turns(turns, turns)
    failure = result["failure_path_b_vs_c"]

    assert result["summary"]["overall_pass"] is False
    assert failure["informational_only"] is True
    assert failure["all_observed_outcomes_compatible"] is True
    assert failure["turns"][0]["relation"] == "exact"
    assert failure["turns"][0]["path_b"] == {
        "class": "network_error",
        "http_status": 200,
        "retry_after": None,
        "terminal_class": "failed",
        "complete": False,
        "incomplete_reason": "network_error",
        "network_error_category": "connection_closed",
    }

    report = build_report(result)
    assert "## Failure-path Outcomes" in report
    assert "B incomplete reason" in report
    assert "B network error" in report
    assert "network_error" in report
    assert "Raw transport exception messages are never retained" in report


def test_http_rejection_failure_outcome_retains_retry_hint() -> None:
    record = _http_sse_record()
    record["transport"] = "http_json"
    record["response"] = {
        "status": 429,
        "headers": {"Retry-After": "3"},
        "body": {"error": {"type": "rate_limit_error"}},
        "body_bytes": 1,
        "done_seen": False,
        "parse_errors": [],
    }
    turns = extract_turns([record])

    result = compare_turns(turns, turns)
    outcome = result["failure_path_b_vs_c"]["turns"][0]["path_b"]

    assert result["summary"]["overall_pass"] is True
    assert outcome["class"] == "http_rejection"
    assert outcome["http_status"] == 429
    assert outcome["retry_after"] == "3"


def test_failure_translation_is_visible_but_remains_informational() -> None:
    rejection = _http_sse_record()
    rejection["transport"] = "http_json"
    rejection["response"] = {
        "status": 503,
        "headers": {},
        "body": {"error": {"type": "service_unavailable"}},
        "body_bytes": 1,
        "done_seen": False,
        "parse_errors": [],
    }
    connection_failure = _http_sse_record()
    connection_failure["response"]["events"] = connection_failure["response"]["events"][:1]
    connection_failure["response"]["network_error"] = {"category": "connection_closed"}

    result = compare_turns(extract_turns([rejection]), extract_turns([connection_failure]))
    failure = result["failure_path_b_vs_c"]

    assert failure["turns"][0]["relation"] == "failure_translation"
    assert failure["turns"][0]["compatible"] is True
    assert failure["informational_only"] is True
    assert result["summary"]["overall_pass"] is False


def test_retry_after_mismatch_is_a_strict_failure() -> None:
    path_b = _http_sse_record()
    path_c = _http_sse_record()
    for record, retry_after in ((path_b, None), (path_c, "3")):
        record["transport"] = "http_json"
        record["response"] = {
            "status": 429,
            "headers": {} if retry_after is None else {"Retry-After": retry_after},
            "body": {"error": {"type": "rate_limit_error"}},
            "body_bytes": 1,
            "done_seen": False,
            "parse_errors": [],
        }

    result = compare_turns(extract_turns([path_b]), extract_turns([path_c]))

    assert result["summary"]["overall_pass"] is False
    assert "retry_after" in {mismatch["category"] for mismatch in result["path_b_vs_c"]["hard_mismatches"]}


def test_failure_outcomes_include_end_to_end_a_vs_b_and_final_outcome() -> None:
    direct = _http_sse_record()
    direct["response"]["events"] = direct["response"]["events"][:1]
    direct["response"]["network_error"] = {"category": "timeout"}
    routed = _http_sse_record()
    routed["response"]["events"][-1] = {
        "event_type": "response.failed",
        "data": {
            "type": "response.failed",
            "response": {"error": {"code": "upstream_request_timeout", "type": "server_error"}},
        },
    }

    result = compare_turns(extract_turns([routed]), extract_turns([routed]), extract_turns([direct]))
    analysis = result["failure_path_a_vs_b"]

    assert analysis["available"] is True
    assert analysis["attempt_counts"] == {"path_a": 1, "path_b": 1}
    assert analysis["turns"][0]["path_a"]["class"] == "network_error"
    assert analysis["turns"][0]["path_b"]["class"] == "failure_terminal"
    assert analysis["final_outcome"]["relation"] == "failure_translation"
    report = build_report(result)
    assert "### End-to-end A↔B" in report
    assert "A network error" in report
