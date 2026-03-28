from __future__ import annotations

import json
import logging

import pytest

from app.core.runtime_logging import JsonFormatter, UtcDefaultFormatter

pytestmark = pytest.mark.unit


@pytest.fixture
def json_formatter():
    return JsonFormatter()


@pytest.fixture
def text_formatter():
    return UtcDefaultFormatter(
        fmt="%(asctime)s %(levelprefix)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        use_colors=None,
    )


def test_json_formatter_produces_valid_json(json_formatter):
    record = logging.LogRecord(
        name="test.module",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    output = json_formatter.format(record)
    parsed = json.loads(output)
    assert isinstance(parsed, dict)


def test_json_formatter_includes_required_fields(json_formatter):
    record = logging.LogRecord(
        name="test.module",
        level=logging.WARNING,
        pathname="test.py",
        lineno=42,
        msg="Test warning",
        args=(),
        exc_info=None,
    )
    output = json_formatter.format(record)
    parsed = json.loads(output)

    assert "timestamp" in parsed
    assert "level" in parsed
    assert "logger" in parsed
    assert "message" in parsed
    assert parsed["level"] == "WARNING"
    assert parsed["logger"] == "test.module"
    assert parsed["message"] == "Test warning"


def test_json_formatter_includes_extra_fields(json_formatter):
    record = logging.LogRecord(
        name="test.module",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-123"
    record.user_id = "user-456"

    output = json_formatter.format(record)
    parsed = json.loads(output)

    assert parsed["request_id"] == "req-123"
    assert parsed["user_id"] == "user-456"


def test_json_formatter_handles_non_serializable_objects(json_formatter):
    record = logging.LogRecord(
        name="test.module",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    class CustomObject:
        def __repr__(self):
            return "<CustomObject>"

    record.custom_field = CustomObject()

    output = json_formatter.format(record)
    parsed = json.loads(output)

    assert "custom_field" in parsed
    assert parsed["custom_field"] == "<CustomObject>"


def test_json_formatter_includes_exception_info(json_formatter):
    try:
        raise ValueError("Test error")
    except ValueError:
        import sys

        exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test.module",
            level=logging.ERROR,
            pathname="test.py",
            lineno=42,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

    output = json_formatter.format(record)
    parsed = json.loads(output)

    assert "exception" in parsed
    assert "ValueError: Test error" in parsed["exception"]


def test_json_formatter_with_formatted_message(json_formatter):
    record = logging.LogRecord(
        name="test.module",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="User %s logged in from %s",
        args=("alice", "192.168.1.1"),
        exc_info=None,
    )
    output = json_formatter.format(record)
    parsed = json.loads(output)

    assert parsed["message"] == "User alice logged in from 192.168.1.1"


def test_text_formatter_not_json(text_formatter):
    record = logging.LogRecord(
        name="test.module",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    output = text_formatter.format(record)

    with pytest.raises(json.JSONDecodeError):
        json.loads(output)

    assert "test.module" in output
    assert "Test message" in output


def test_json_formatter_timestamp_is_iso_format(json_formatter):
    record = logging.LogRecord(
        name="test.module",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    output = json_formatter.format(record)
    parsed = json.loads(output)

    timestamp = parsed["timestamp"]
    assert "T" in timestamp
    assert "+" in timestamp or "Z" in timestamp or timestamp.endswith("00:00")
