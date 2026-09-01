from __future__ import annotations

import json
import logging
import sys

import pytest

from app.core.runtime_logging import (
    JsonFormatter,
    UtcDefaultFormatter,
    _error_log_field,
    _redact_log_value,
    build_log_config,
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


def test_error_log_field_quotes_redacted_field_values():
    value = "temporary failure status=200 request_id=req-1 api_key=abc123"

    field = _error_log_field(value)

    assert field == '"temporary failure status=200 request_id=req-1 api_key=[REDACTED]"'


@pytest.mark.parametrize(
    "value, expected",
    [
        ('provider error {"api_key":"sk-secret"}', 'provider error {"api_key":"[REDACTED]"}'),
        ('provider error {"authorization":"Basic dXNlcjpwYXNz"}', 'provider error {"authorization":"[REDACTED]"}'),
    ],
)
def test_error_log_field_redacts_json_style_secrets(value, expected):
    assert _error_log_field(value) == json.dumps(expected)


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

    monkeypatch.setenv("CODEX_LB_LOG_FORMAT", "json")
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

    monkeypatch.setenv("CODEX_LB_LOG_FORMAT", "text")
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

    monkeypatch.setenv("CODEX_LB_LOG_FORMAT", "text")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    config = build_log_config()
    root_logger = cast(dict, config.get("root", {}))

    assert root_logger.get("handlers") == ["default"]
    assert root_logger.get("level") == "INFO"
    get_settings.cache_clear()


def test_redact_log_value_keeps_one_line_normalization():
    value = "token=abc123\n  status=failed"

    assert _redact_log_value(value) == "token=[REDACTED] status=failed"


def test_redact_log_value_redacts_unterminated_json_secret_through_field_end():
    value = 'provider error {"api_key":"sk-QA_TRUNCATED'

    assert _redact_log_value(value) == 'provider error {"api_key":"[REDACTED]'


@pytest.mark.parametrize(
    "value, expected",
    [
        ("authorization=Bearer user:QA_SECRET, status=ok", "authorization=Bearer [REDACTED], status=ok"),
        ("Bearer abc?QA_SECRET&status=ok", "Bearer [REDACTED]&status=ok"),
        ('{"message":"Bearer QA_SECRET","status":"ok"}', '{"message":"Bearer [REDACTED]","status":"ok"}'),
        ("(Bearer QA_SECRET); retrying", "(Bearer [REDACTED]); retrying"),
        ("Bearer [REDACTED]QA_SECRET", "Bearer [REDACTED]"),
    ],
)
def test_redact_log_value_bounds_bearer_credentials_at_structural_delimiters(value, expected):
    assert _redact_log_value(value) == expected
    assert _redact_log_value(expected) == expected


@pytest.fixture(params=["text", "json"])
def exception_formatter(request, text_formatter, json_formatter):
    return text_formatter if request.param == "text" else json_formatter


def _exception_record(exc_info) -> logging.LogRecord:
    return logging.LogRecord(
        name="test.module",
        level=logging.ERROR,
        pathname="test.py",
        lineno=42,
        msg="Error occurred",
        args=(),
        exc_info=exc_info,
    )


def _exception_text(formatter: logging.Formatter, record: logging.LogRecord) -> str:
    output = formatter.format(record)
    if isinstance(formatter, JsonFormatter):
        return json.loads(output)["exception"]
    return output.split("\n", 1)[1]


def _formatted_traceback(formatter: logging.Formatter, message: str) -> str:
    try:
        raise ValueError(message)
    except ValueError:
        record = _exception_record(sys.exc_info())
    return _exception_text(formatter, record)


def _assert_only_secret_lines_changed(redacted_text: str, plain_text: str) -> None:
    redacted_lines = redacted_text.splitlines(keepends=True)
    plain_lines = plain_text.splitlines(keepends=True)
    assert len(redacted_lines) == len(plain_lines)
    for redacted, plain in zip(redacted_lines, plain_lines, strict=True):
        if "QA_" in plain:
            assert "QA_" not in redacted
            assert "[REDACTED]" in redacted
            assert redacted.endswith(plain[len(plain.splitlines()[0]) :])
        else:
            assert redacted == plain


def test_exception_traceback_redacts_recognized_secrets(exception_formatter):
    message = 'api_key=sk-QA_KEY Authorization: Bearer QA_BEARER body={"token":"QA_JSON"}'

    output = _formatted_traceback(exception_formatter, message)

    for secret in ("sk-QA_KEY", "QA_BEARER", "QA_JSON"):
        assert secret not in output
    assert 'ValueError: api_key=[REDACTED] Authorization: Bearer [REDACTED] body={"token":"[REDACTED]"}' in output
    assert output.startswith("Traceback (most recent call last):\n  File ")


@pytest.mark.parametrize(
    "message, expected",
    [
        ("authorization=Basic user:QA_SECRET, status=ok", "authorization=[REDACTED], status=ok"),
        ("authorization=Bearer user:QA_SECRET, status=ok", "authorization=Bearer [REDACTED], status=ok"),
        ("authorization=Bearer abc?QA_SECRET&status=ok", "authorization=Bearer [REDACTED]&status=ok"),
        (
            "authorization=Digest username=public; response=QA_SECRET, status=ok",
            "authorization=[REDACTED], status=ok",
        ),
    ],
)
def test_exception_traceback_redacts_delimited_credentials_and_keeps_later_fields(
    exception_formatter, message, expected
):
    output = _formatted_traceback(exception_formatter, message)

    assert "QA_SECRET" not in output
    assert f"ValueError: {expected}" in output
    assert _redact_log_value(expected) == expected


@pytest.mark.parametrize(
    "terminator", ["\n", "\r\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"]
)
def test_exception_traceback_redaction_stops_at_line_ends(exception_formatter, terminator):
    message = f"authorization=Basic QA_SECRET{terminator}status=failed{terminator}api_key=QA_KEY"

    output = _formatted_traceback(exception_formatter, message)

    assert "QA_SECRET" not in output
    assert "QA_KEY" not in output
    assert f"authorization=[REDACTED]{terminator}status=failed{terminator}api_key=[REDACTED]" in output


def test_exception_traceback_redaction_keeps_chained_exception_structure(exception_formatter):
    try:
        try:
            raise KeyError("password=QA_CAUSE")
        except KeyError as error:
            raise ValueError("token=QA_EFFECT") from error
    except ValueError:
        exc_info = sys.exc_info()
    plain_text = logging.Formatter().formatException(exc_info)

    redacted_text = _exception_text(exception_formatter, _exception_record(exc_info))

    assert "\nThe above exception was the direct cause of the following exception:\n" in redacted_text
    _assert_only_secret_lines_changed(redacted_text, plain_text)


@pytest.mark.parametrize(
    "message, expected",
    [
        ('{"authorization":"QA_SECRET\nsafe diagnostic line"}', '{"authorization":"[REDACTED]\nsafe diagnostic line"}'),
        ('{"token":"QA_SECRET\\\nsafe diagnostic line"}', '{"token":"[REDACTED]\nsafe diagnostic line"}'),
    ],
)
def test_exception_traceback_redacts_json_value_cut_by_line_end(exception_formatter, message, expected):
    output = _formatted_traceback(exception_formatter, message)

    assert "QA_SECRET" not in output
    assert expected in output


def test_exception_traceback_unterminated_json_value_keeps_following_frames(exception_formatter):
    try:
        try:
            raise KeyError('{"token":"QA_SECRET')
        except KeyError as error:
            raise ValueError("wrapped") from error
    except ValueError:
        exc_info = sys.exc_info()
    plain_text = logging.Formatter().formatException(exc_info)

    redacted_text = _exception_text(exception_formatter, _exception_record(exc_info))

    assert '\nKeyError: \'{"token":"[REDACTED]\n' in redacted_text
    _assert_only_secret_lines_changed(redacted_text, plain_text)


def test_text_formatter_redacts_traceback_cached_by_another_formatter(text_formatter):
    try:
        raise ValueError("api_key=QA_CACHED")
    except ValueError:
        record = _exception_record(sys.exc_info())
    plain_output = logging.Formatter().format(record)
    assert "QA_CACHED" in plain_output
    assert record.exc_text is not None and "QA_CACHED" in record.exc_text

    output = text_formatter.format(record)

    assert "QA_CACHED" not in output
    assert "api_key=[REDACTED]" in output
    assert "QA_CACHED" in record.exc_text


def test_text_formatter_does_not_cache_traceback_on_shared_record(text_formatter):
    try:
        raise ValueError("api_key=QA_FRESH")
    except ValueError:
        record = _exception_record(sys.exc_info())

    output = text_formatter.format(record)

    assert "api_key=[REDACTED]" in output
    assert record.exc_text is None


def test_text_formatter_redacts_cached_traceback_without_exc_info(text_formatter):
    record = _exception_record(None)
    record.exc_text = 'Traceback (most recent call last):\n  File "x.py", line 1, in f\nValueError: token=QA_CACHED\n'

    output = text_formatter.format(record)

    assert "QA_CACHED" not in output
    assert output.endswith(
        '\nTraceback (most recent call last):\n  File "x.py", line 1, in f\nValueError: token=[REDACTED]\n'
    )
