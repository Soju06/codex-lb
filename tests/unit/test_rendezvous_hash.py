from __future__ import annotations

from collections import Counter

import pytest

from app.core.balancer.rendezvous_hash import select_node

pytestmark = pytest.mark.unit


def test_deterministic() -> None:
    nodes = ["a", "b", "c"]
    assert select_node("key", nodes) == select_node("key", nodes)


def test_ring_add_moves_only_1_over_N_keys() -> None:
    base_ring = ["a", "b", "c", "d", "e"]
    expanded_ring = [*base_ring, "f"]
    keyspace = [f"k-{index}" for index in range(1000)]

    before = {key: select_node(key, base_ring) for key in keyspace}
    after = {key: select_node(key, expanded_ring) for key in keyspace}
    remapped = sum(1 for key in keyspace if before[key] != after[key])

    assert remapped <= 200


def test_ring_remove_moves_only_1_over_N_keys() -> None:
    base_ring = ["a", "b", "c", "d", "e"]
    reduced_ring = ["a", "b", "c", "d"]
    keyspace = [f"k-{index}" for index in range(1000)]

    before = {key: select_node(key, base_ring) for key in keyspace}
    after = {key: select_node(key, reduced_ring) for key in keyspace}
    remapped = sum(1 for key in keyspace if before[key] != after[key])

    assert remapped <= 250


def test_empty_ring_returns_none() -> None:
    assert select_node("key", []) is None


def test_single_node() -> None:
    assert select_node("key-a", ["only"]) == "only"
    assert select_node("key-b", ["only"]) == "only"
    assert select_node("another", ["only"]) == "only"


def test_even_distribution() -> None:
    nodes = ["a", "b", "c", "d", "e"]
    owners = Counter(select_node(f"k-{index}", nodes) for index in range(10000))

    for node in nodes:
        assert 1500 <= owners[node] <= 2500
