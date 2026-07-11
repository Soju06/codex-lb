from __future__ import annotations

import pytest

from app.core.utils.request_id import (
    ensure_request_id,
    ensure_request_scope_id,
    get_request_id,
    get_request_scope_id,
    reset_request_id,
    reset_request_scope_id,
    set_request_id,
    set_request_scope_id,
)

pytestmark = pytest.mark.unit


def test_ensure_request_id_uses_explicit_value() -> None:
    token = set_request_id(None)
    try:
        assert ensure_request_id("req-explicit") == "req-explicit"
        assert get_request_id() == "req-explicit"
    finally:
        reset_request_id(token)


def test_ensure_request_id_generates_and_reuses_request_id() -> None:
    token = set_request_id(None)
    try:
        request_id = ensure_request_id()
        assert request_id == ensure_request_id()
        assert get_request_id() == request_id
        assert request_id
    finally:
        reset_request_id(token)


def test_ensure_request_scope_id_generates_and_reuses_server_scope() -> None:
    token = set_request_scope_id(None)
    try:
        request_scope_id = ensure_request_scope_id()
        assert request_scope_id == ensure_request_scope_id()
        assert get_request_scope_id() == request_scope_id
        assert request_scope_id
    finally:
        reset_request_scope_id(token)
