"""Repeated-sample ClientHello extension-order analysis.

Raw JA3 hashes are useful observations, but rustls may randomize extension
serialization.  This module therefore keeps stable capabilities exact and
calibrates order differences against two independently captured direct-Codex
cohorts.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.traffic_analysis.turns import load_capture
except ModuleNotFoundError:  # Allow direct script imports.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.turns import load_capture


TLS_TRANSPORTS = ("http_json", "http_sse", "websocket")
DEFAULT_MIN_SAMPLES = 20
_BOOTSTRAP_ITERATIONS = 2_000
_BOOTSTRAP_PERCENTILE = 0.95
_PSK_EXTENSION = 41
_GREASE_SENTINEL = -1


def _is_grease(value: int) -> bool:
    return value & 0x0F0F == 0x0A0A


def _normalize_tls_id(value: Any) -> Any:
    return _GREASE_SENTINEL if isinstance(value, int) and _is_grease(value) else value


def _normalized_ids(values: Any, *, remove_psk: bool = False) -> list[Any] | None:
    if not isinstance(values, list):
        return None
    return [_normalize_tls_id(value) for value in values if not (remove_psk and value == _PSK_EXTENSION)]


def stable_tls_profile(tls: Any) -> Any:
    """Project TLS evidence onto order-insensitive, per-connection invariants."""

    if not isinstance(tls, Mapping):
        return tls
    profile: dict[str, Any] = {key: tls.get(key) for key in ("alpn", "version", "selected_cipher")}
    hello = tls.get("client_hello")
    if not isinstance(hello, Mapping):
        return profile

    lengths = hello.get("extension_lengths")
    normalized_lengths = None
    if isinstance(lengths, list):
        normalized_lengths = sorted(
            (
                {
                    "type": _normalize_tls_id(item.get("type")),
                    "bytes": item.get("bytes"),
                }
                for item in lengths
                if isinstance(item, Mapping) and item.get("type") != _PSK_EXTENSION
            ),
            key=lambda item: (str(item["type"]), str(item["bytes"])),
        )

    profile["client_hello"] = {
        "sni": hello.get("sni"),
        "offered_alpn": hello.get("offered_alpn"),
        "legacy_version": hello.get("legacy_version"),
        "ciphers": _normalized_ids(hello.get("ciphers")),
        "extensions": sorted(_normalized_ids(hello.get("extensions"), remove_psk=True) or [], key=str),
        "extension_lengths": normalized_lengths,
        "supported_groups": _normalized_ids(hello.get("supported_groups")),
        "point_formats": hello.get("point_formats"),
        "signature_algorithms": _normalized_ids(hello.get("signature_algorithms")),
        "key_share_groups": _normalized_ids(hello.get("key_share_groups")),
    }
    return profile


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hello_samples(records: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    samples = {transport: [] for transport in TLS_TRANSPORTS}
    seen: dict[str, set[str]] = {transport: set() for transport in TLS_TRANSPORTS}
    for record in records:
        transport = record.get("transport")
        if transport not in samples:
            continue
        network = record.get("network")
        tls = network.get("tls") if isinstance(network, Mapping) else None
        hello = tls.get("client_hello") if isinstance(tls, Mapping) else None
        if not isinstance(tls, Mapping) or not isinstance(hello, Mapping):
            continue
        connection_key = hello.get("client_hello_sha256")
        if not isinstance(connection_key, str) or not connection_key:
            connection_key = hashlib.sha256(_canonical(hello).encode("utf-8")).hexdigest()
        if connection_key in seen[transport]:
            continue
        seen[transport].add(connection_key)
        samples[transport].append(
            {
                "tls": {str(key): value for key, value in tls.items()},
                "hello": {str(key): value for key, value in hello.items()},
            }
        )
    return samples


def _orders(samples: Sequence[Mapping[str, Any]]) -> list[tuple[Any, ...]]:
    orders: list[tuple[Any, ...]] = []
    for sample in samples:
        hello = sample.get("hello")
        extensions = hello.get("extensions") if isinstance(hello, Mapping) else None
        normalized = _normalized_ids(extensions, remove_psk=True)
        if normalized:
            orders.append(tuple(normalized))
    return orders


def _pairwise_probabilities(orders: Sequence[tuple[Any, ...]]) -> dict[tuple[Any, Any], float]:
    if not orders:
        return {}
    universe = sorted(set.intersection(*(set(order) for order in orders)), key=str)
    counts: Counter[tuple[Any, Any]] = Counter()
    pairs = [(left, right) for index, left in enumerate(universe) for right in universe[index + 1 :]]
    for order in orders:
        positions = {extension: index for index, extension in enumerate(order)}
        for pair in pairs:
            if positions[pair[0]] < positions[pair[1]]:
                counts[pair] += 1
    return {pair: counts[pair] / len(orders) for pair in pairs}


def _matrix_distance(left: Sequence[tuple[Any, ...]], right: Sequence[tuple[Any, ...]]) -> float | None:
    left_matrix = _pairwise_probabilities(left)
    right_matrix = _pairwise_probabilities(right)
    pairs = sorted(set(left_matrix) & set(right_matrix), key=str)
    if not pairs:
        return None
    return sum(abs(left_matrix[pair] - right_matrix[pair]) for pair in pairs) / len(pairs)


def _order_entropy(orders: Sequence[tuple[Any, ...]]) -> float | None:
    matrix = _pairwise_probabilities(orders)
    if not matrix:
        return None
    entropy = 0.0
    for probability in matrix.values():
        if probability not in {0.0, 1.0}:
            entropy -= probability * math.log2(probability)
            entropy -= (1.0 - probability) * math.log2(1.0 - probability)
    return entropy / len(matrix)


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def _bootstrap_limit(
    direct_pool: Sequence[tuple[Any, ...]],
    *,
    left_size: int,
    right_size: int,
    iterations: int = _BOOTSTRAP_ITERATIONS,
) -> float | None:
    if not direct_pool or left_size < 1 or right_size < 1:
        return None
    seed_material = _canonical([direct_pool, left_size, right_size, iterations]).encode("utf-8")
    rng = random.Random(int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big"))
    distances: list[float] = []
    for _ in range(iterations):
        left = [direct_pool[rng.randrange(len(direct_pool))] for _ in range(left_size)]
        right = [direct_pool[rng.randrange(len(direct_pool))] for _ in range(right_size)]
        distance = _matrix_distance(left, right)
        if distance is not None:
            distances.append(distance)
    return _percentile(distances, _BOOTSTRAP_PERCENTILE) if distances else None


def _cohort_summary(samples: Sequence[Mapping[str, Any]], orders: Sequence[tuple[Any, ...]]) -> dict[str, Any]:
    profiles = Counter(_canonical(stable_tls_profile(sample.get("tls"))) for sample in samples)
    ja3_counts = Counter(
        str(hello.get("ja3_md5"))
        for sample in samples
        if isinstance((hello := sample.get("hello")), Mapping) and hello.get("ja3_md5")
    )
    return {
        "samples": len(samples),
        "stable_profile_count": len(profiles),
        "unique_extension_orders": len(set(orders)),
        "pairwise_order_entropy": _order_entropy(orders),
        "unique_ja3_md5": len(ja3_counts),
        "ja3_md5_counts": dict(sorted(ja3_counts.items())),
    }


def _analyze_transport(
    reference_samples: Sequence[Mapping[str, Any]],
    direct_samples: Sequence[Mapping[str, Any]],
    candidate_samples: Sequence[Mapping[str, Any]],
    *,
    min_samples: int,
) -> dict[str, Any]:
    reference_orders = _orders(reference_samples)
    direct_orders = _orders(direct_samples)
    candidate_orders = _orders(candidate_samples)
    cohorts = {
        "path_a_reference": _cohort_summary(reference_samples, reference_orders),
        "path_a": _cohort_summary(direct_samples, direct_orders),
        "path_c": _cohort_summary(candidate_samples, candidate_orders),
    }
    counts = {name: int(summary["samples"]) for name, summary in cohorts.items()}
    if any(count < min_samples for count in counts.values()):
        return {
            "matches": None,
            "status": "unobserved",
            "reason": "insufficient_independent_client_hellos",
            "minimum_samples": min_samples,
            "cohorts": cohorts,
        }

    reference_profiles = {_canonical(stable_tls_profile(sample.get("tls"))) for sample in reference_samples}
    direct_profiles = {_canonical(stable_tls_profile(sample.get("tls"))) for sample in direct_samples}
    candidate_profiles = {_canonical(stable_tls_profile(sample.get("tls"))) for sample in candidate_samples}
    stable_match = len(reference_profiles) == len(direct_profiles) == len(candidate_profiles) == 1 and (
        reference_profiles == direct_profiles == candidate_profiles
    )
    baseline_distance = _matrix_distance(reference_orders, direct_orders)
    candidate_distance = _matrix_distance(direct_orders, candidate_orders)
    bootstrap_limit = _bootstrap_limit(
        [*reference_orders, *direct_orders],
        left_size=len(direct_orders),
        right_size=len(candidate_orders),
    )
    acceptance_limit = (
        max(value for value in (baseline_distance, bootstrap_limit) if value is not None)
        if baseline_distance is not None or bootstrap_limit is not None
        else None
    )
    order_match = (
        candidate_distance <= acceptance_limit + 1e-12
        if candidate_distance is not None and acceptance_limit is not None
        else None
    )
    matches = stable_match and order_match is True
    return {
        "matches": matches,
        "status": "match" if matches else "mismatch",
        "stable_profiles_match": stable_match,
        "extension_order_matches_direct_variance": order_match,
        "baseline_a_reference_vs_a_distance": baseline_distance,
        "candidate_a_vs_c_distance": candidate_distance,
        "direct_bootstrap_p95_limit": bootstrap_limit,
        "acceptance_limit": acceptance_limit,
        "bootstrap_iterations": _BOOTSTRAP_ITERATIONS,
        "minimum_samples": min_samples,
        "cohorts": cohorts,
    }


def analyze_tls_randomization_records(
    path_a_reference_records: Sequence[Mapping[str, Any]],
    path_a_records: Sequence[Mapping[str, Any]],
    path_c_records: Sequence[Mapping[str, Any]],
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict[str, Any]:
    """Compare direct/direct and direct/LB ClientHello sample distributions."""

    if min_samples < 2:
        raise ValueError("TLS randomization analysis requires at least 2 samples per cohort")
    reference = _hello_samples(path_a_reference_records)
    direct = _hello_samples(path_a_records)
    candidate = _hello_samples(path_c_records)
    transports = {
        transport: _analyze_transport(
            reference[transport],
            direct[transport],
            candidate[transport],
            min_samples=min_samples,
        )
        for transport in TLS_TRANSPORTS
    }
    observed = [item["matches"] for item in transports.values() if item["matches"] is not None]
    return {
        "available": True,
        "informational_only": True,
        "all_observed_transports_match": all(observed) if observed else None,
        "unobserved_transports": [name for name, item in transports.items() if item["matches"] is None],
        "transports": transports,
    }


def analyze_tls_randomization_paths(
    path_a_reference: str,
    path_a: str,
    path_c: str,
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict[str, Any]:
    """Load three capture files and analyze extension-order randomization."""

    try:
        return analyze_tls_randomization_records(
            load_capture(path_a_reference, strict=True),
            load_capture(path_a, strict=True),
            load_capture(path_c, strict=True),
            min_samples=min_samples,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "informational_only": True,
            "reason": "capture_error",
            "error": str(exc),
            "all_observed_transports_match": None,
            "unobserved_transports": list(TLS_TRANSPORTS),
            "transports": {},
        }
