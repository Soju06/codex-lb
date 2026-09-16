from __future__ import annotations

import json
import logging

import pytest
from starlette.requests import Request

from app.core.runtime_logging import (
    JsonFormatter,
    UtcDefaultFormatter,
    _redact_log_value,
    build_log_config,
    log_error_response,
)

pytestmark = pytest.mark.unit


def test_redact_log_value_masks_keyed_secrets_and_bearer_tokens():
    value = "password=secret-token Authorization: Bearer abc.def api_key=abc123"

    redacted = _redact_log_value(value)

    assert redacted == "password=[REDACTED] Authorization: Bearer [REDACTED] api_key=[REDACTED]"


def test_redact_log_value_masks_basic_authorization_credentials():
    value = "Authorization: Basic dXNlcjpwYXNz, status=failed"

    redacted = _redact_log_value(value)

    assert redacted == "Authorization: [REDACTED], status=failed"


def _request(path: str = "/backend-api/codex/responses") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
        }
    )


def test_log_error_response_includes_structured_error_detail_without_secrets(caplog):
    logger = logging.getLogger("tests.structured_logging")
    caplog.set_level(logging.WARNING, logger=logger.name)

    log_error_response(
        logger,
        _request(),
        429,
        "account_stream_cap",
        "Bearer abc.def token=secret saturated",
        category="proxy_error_response",
    )

    record = next(record for record in caplog.records if record.name == logger.name)
    assert record.error_code == "account_stream_cap"
    assert record.error_message == "Bearer [REDACTED] token=[REDACTED] saturated"
    assert record.error_category == "proxy_error_response"
    assert record.path == "/backend-api/codex/responses"
    assert "code=account_stream_cap" in record.getMessage()
    assert "Bearer abc.def" not in record.getMessage()
    assert "token=secret" not in record.getMessage()


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


def test_build_log_config_uses_json_access_formatter_when_json(monkeypatch):
    """build_log_config() should use JsonAccessFormatter when log_format == 'json'."""
    from typing import cast

    monkeypatch.setenv("AGENT_LB_LOG_FORMAT", "json")
    # Clear lru_cache so the setting is re-read
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    config = build_log_config()
    formatters = cast(dict, config.get("formatters", {}))
    access_formatter = cast(dict, formatters.get("access", {}))
    assert access_formatter.get("()") == "app.core.runtime_logging.JsonAccessFormatter"
    # Restore
    get_settings.cache_clear()


def test_build_log_config_uses_utc_access_formatter_when_text(monkeypatch):
    """build_log_config() should use UtcAccessFormatter when log_format == 'text'."""
    from typing import cast

    monkeypatch.setenv("AGENT_LB_LOG_FORMAT", "text")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    config = build_log_config()
    formatters = cast(dict, config.get("formatters", {}))
    access_formatter = cast(dict, formatters.get("access", {}))
    assert access_formatter.get("()") == "app.core.runtime_logging.UtcAccessFormatter"
    # Restore
    get_settings.cache_clear()


def test_build_log_config_exposes_app_loggers_via_root_handler(monkeypatch):
    from typing import cast

    monkeypatch.setenv("AGENT_LB_LOG_FORMAT", "text")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    config = build_log_config()
    root_logger = cast(dict, config.get("root", {}))

    assert root_logger.get("handlers") == ["default"]
    assert root_logger.get("level") == "INFO"
    get_settings.cache_clear()


def test_build_log_config_queues_stream_handlers(monkeypatch):
    from typing import cast

    monkeypatch.setenv("AGENT_LB_LOG_FORMAT", "text")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    config = build_log_config()
    handlers = cast(dict, config["handlers"])
    for name in ("default", "access"):
        queued = cast(dict, handlers[name])
        assert queued["class"] == "logging.handlers.QueueHandler"
        assert queued["handlers"] == [f"{name}_stream"]
        stream = cast(dict, handlers[f"{name}_stream"])
        assert stream["class"] == "logging.StreamHandler"
    get_settings.cache_clear()


def test_configure_runtime_logging_emits_through_listener(monkeypatch, capsys):
    """A record logged on the caller's thread reaches stderr via the listener
    thread, formatted by the stream handler (asctime + level + message)."""
    import logging as _logging
    import time as _time

    from app.core.runtime_logging import configure_runtime_logging, start_log_listeners

    monkeypatch.setenv("AGENT_LB_LOG_FORMAT", "text")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    root = _logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        configure_runtime_logging()
        queue_handler = _logging.getHandlerByName("default")
        assert queue_handler.__class__.__name__ == "QueueHandler"
        listeners = start_log_listeners()  # idempotent: already started
        assert listeners and all(getattr(item, "_thread", None) is not None for item in listeners)
        _logging.getLogger("app.test.queued").info("queued-hello %s", 42)
        deadline = _time.monotonic() + 5
        while _time.monotonic() < deadline:
            err = capsys.readouterr().err
            if "queued-hello 42" in err:
                break
            _time.sleep(0.02)
        else:
            raise AssertionError("listener never flushed the record")
        assert "app.test.queued" in err
    finally:
        for listener in start_log_listeners():
            listener.stop()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        get_settings.cache_clear()
