from __future__ import annotations

import io
import json
import logging
import re

import pytest

import app.core.runtime_logging as runtime_logging
from app.core.runtime_logging import (
    JsonAccessFormatter,
    JsonFormatter,
    UtcAccessFormatter,
    UtcDefaultFormatter,
    _error_log_field,
    _redact_log_value,
    build_log_config,
)
from tests.unit._proxy_test_helpers import runtime_basic_auth_url

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


# --- rendered-record redaction backstop -------------------------------------


_PROXY_AUTHORITY = "183.110.26.193:6014"


def _connection_key_line(proxy_url: str) -> str:
    # Exact shape uvloop's default exception handler logs for aiohttp
    # Connection.__del__ (evidence: prod 'Unclosed connection' ERROR lines).
    return (
        "Unclosed connection\n"
        "client_connection: Connection<ConnectionKey(host='chatgpt.com', port=443, is_ssl=True, ssl=True, "
        f"proxy=URL('{proxy_url}'), proxy_auth=None, proxy_headers_hash=None, server_hostname=None)>"
    )


def _formatter_from_config(monkeypatch, log_format: str, name: str = "default") -> logging.Formatter:
    import importlib
    from typing import cast

    from app.core.config.settings import get_settings

    monkeypatch.setenv("CODEX_LB_LOG_FORMAT", log_format)
    get_settings.cache_clear()
    try:
        spec = dict(cast(dict, cast(dict, build_log_config()["formatters"])[name]))
    finally:
        get_settings.cache_clear()
    module_name, _, class_name = spec.pop("()").rpartition(".")
    return getattr(importlib.import_module(module_name), class_name)(**spec)


def _render(formatter: logging.Formatter, record: logging.LogRecord) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    handler.handle(record)
    return stream.getvalue()


def _record(msg: str, *, level: int = logging.ERROR, name: str = "asyncio", exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(name, level, "connector.py", 1, msg, (), exc_info)


@pytest.mark.parametrize("log_format", ["text", "json"])
@pytest.mark.parametrize("scheme", ["http", "https"])
def test_rendered_unclosed_connection_line_redacts_proxy_userinfo(monkeypatch, log_format, scheme):
    formatter = _formatter_from_config(monkeypatch, log_format)
    proxy_url = runtime_basic_auth_url("smart-user", "SECRETPW", _PROXY_AUTHORITY).replace("http://", f"{scheme}://", 1)

    output = _render(formatter, _record(_connection_key_line(proxy_url)))

    assert "SECRETPW" not in output
    assert f"[REDACTED]@{_PROXY_AUTHORITY}" in output
    assert "Unclosed connection" in output
    if log_format == "json":
        assert json.loads(output)["message"] == _connection_key_line(f"{scheme}://[REDACTED]@{_PROXY_AUTHORITY}")


@pytest.mark.parametrize("log_format", ["text", "json"])
@pytest.mark.parametrize("password", ["sq'uote", 'dq"uote', "!$&()*+,;=", "at@sign", "sp ace"])
def test_rendered_unclosed_connection_line_redacts_yarl_encoded_userinfo(monkeypatch, log_format, password):
    # aiohttp reprs the proxy URL through yarl, which leaves RFC 3986 sub-delims
    # (``'`` included) unencoded in userinfo and percent-encodes the rest.
    from urllib.parse import quote

    from yarl import URL

    formatter = _formatter_from_config(monkeypatch, log_format)
    proxy_url = str(URL(f"https://smart-user:{quote(password, safe='')}@{_PROXY_AUTHORITY}"))

    output = _render(formatter, _record(_connection_key_line(proxy_url), level=logging.INFO))

    assert password not in output
    assert quote(password, safe="") not in output
    assert f"https://[REDACTED]@{_PROXY_AUTHORITY}" in output


def test_redact_rendered_log_text_masks_raw_userinfo_with_apostrophe():
    # Raw ``trust_env`` proxy strings are not percent-encoded at all.
    line = "probe " + runtime_basic_auth_url("smart-user", "sq'uote", "h:1") + " failed"

    assert runtime_logging.redact_rendered_log_text(line, keyed_secrets=False) == "probe http://[REDACTED]@h:1 failed"
    assert _redact_log_value(line) == "probe http://[REDACTED]@h:1 failed"


def test_redact_rendered_log_text_leaves_quoted_host_only_url_before_quoted_email_alone():
    line = "proxy=URL('http://183.110.26.193:6014'), owner='ops@example.com'"

    assert runtime_logging.redact_rendered_log_text(line) == line


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_rendered_basic_auth_repr_redacts_password(monkeypatch, log_format):
    formatter = _formatter_from_config(monkeypatch, log_format)
    line = _connection_key_line(f"http://{_PROXY_AUTHORITY}").replace(
        "proxy_auth=None",
        "proxy_auth=BasicAuth(login='smart-user', password='SECRETPW', encoding='latin1')",
    )

    output = _render(formatter, _record(line))

    assert "SECRETPW" not in output
    assert "password=[REDACTED], encoding='latin1'" in output


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_rendered_exception_traceback_redacts_userinfo(monkeypatch, log_format):
    import sys

    formatter = _formatter_from_config(monkeypatch, log_format)
    password = "TRACEPW"  # kept off the raising line so the traceback source cannot echo it
    try:
        raise RuntimeError(runtime_basic_auth_url("u", password, "h") + "/")
    except RuntimeError:
        record = _record("request failed", name="app.core.clients.codex", exc_info=sys.exc_info())

    output = _render(formatter, record)

    assert "TRACEPW" not in output
    assert "RuntimeError: http://[REDACTED]@h/" in output


def test_redact_rendered_log_text_never_throws_and_fails_closed(monkeypatch, text_formatter, json_formatter):
    class _Exploding:
        def sub(self, *args, **kwargs):
            raise RuntimeError("regex engine failure")

    monkeypatch.setattr(runtime_logging, "_USERINFO_PATTERN", _Exploding())
    line = _connection_key_line(runtime_basic_auth_url("u", "pw", _PROXY_AUTHORITY))
    placeholder = runtime_logging._REDACTION_FAILED_PLACEHOLDER

    assert runtime_logging.redact_rendered_log_text(line) == placeholder

    text_output = _render(text_formatter, _record(line))
    assert text_output.endswith(placeholder + "\n")
    assert "pw" not in text_output
    assert " ERROR asyncio " in text_output

    json_output = json.loads(_render(json_formatter, _record(line)))
    assert json_output["message"] == placeholder
    assert json_output["level"] == "ERROR"
    assert json_output["logger"] == "asyncio"
    assert "pw" not in json.dumps(json_output)


@pytest.mark.parametrize("level", [logging.INFO, logging.ERROR])
def test_username_only_url_userinfo_is_redacted(text_formatter, json_formatter, level):
    # Token-as-username URLs (``https://ghp_xxx@host``) carry the secret without a
    # ``:password`` part; the userinfo pattern must not require the colon.
    line = "proxy=URL('http://tok3n-as-user@proxy.test:8080') status=failed"
    record = _record(line, level=level)

    text_output = _render(text_formatter, record)
    json_output = json.loads(_render(json_formatter, record))

    assert "tok3n-as-user" not in text_output
    assert "proxy=URL('http://[REDACTED]@proxy.test:8080') status=failed" in text_output
    assert json_output["message"] == "proxy=URL('http://[REDACTED]@proxy.test:8080') status=failed"


def test_credential_free_info_line_skips_regex_passes(monkeypatch, text_formatter):
    calls: list[str] = []

    class _Spy:
        def sub(self, *args, **kwargs):
            calls.append("userinfo")
            raise AssertionError("precheck must short-circuit")

    monkeypatch.setattr(runtime_logging, "_USERINFO_PATTERN", _Spy())
    monkeypatch.setattr(runtime_logging, "_redact_secret_patterns", lambda text: calls.append("keyed") or text)
    line = ("http_bridge_forward request_id=req_1 max_output_tokens=32768 route_mode=account_bound status=200 " * 6)[
        :500
    ]

    output = _render(text_formatter, _record(line, level=logging.INFO, name="app.modules.proxy"))

    assert line in output
    assert calls == []


def test_warning_records_apply_keyed_secret_patterns(text_formatter):
    output = _render(text_formatter, _record("refresh failed password=SECRETPW code=401", level=logging.WARNING))

    assert "SECRETPW" not in output
    assert "password=[REDACTED] code=401" in output


def test_warning_records_redact_python_repr_secret_keyed_mappings(text_formatter):
    config = {
        "password": "REPRPW",
        "proxy_password": "it's",
        "api_key": 123,
        "secret": b"BYTESPW",
        "access_token": ["LISTPW", "LISTPW2"],
        "client_secret": None,
        "attempt": 1,
        "tokens": 7,
    }

    record = _record("config %r", level=logging.WARNING)
    record.args = (config,)
    output = _render(text_formatter, record)

    for leaked in ("REPRPW", "it's", "BYTESPW", "LISTPW"):
        assert leaked not in output
    assert "'password': '[REDACTED]'" in output
    assert "'proxy_password': \"[REDACTED]\"" in output
    assert "'api_key': [REDACTED]" in output
    assert "'secret': [REDACTED]" in output
    assert "'access_token': [REDACTED]" in output
    assert "'client_secret': [REDACTED]" in output
    assert "'attempt': 1, 'tokens': 7" in output


def test_info_records_keep_python_repr_secret_keyed_mappings(text_formatter):
    # Keyed patterns are a WARNING+ policy; INFO renders the repr untouched.
    record = _record("config %r", level=logging.INFO)
    record.args = ({"password": "REPRPW"},)

    output = _render(text_formatter, record)

    assert "{'password': 'REPRPW'}" in output


def test_authorization_midline_redaction_truncates_to_separator(text_formatter):
    # Pins the existing pattern-2 behavior: the redaction consumes the rest of
    # the line up to ',' or '&', so trailing fields on the same line are lost.
    output = _render(text_formatter, _record("upstream rejected Authorization: Basic dXNlcjpwYXNz status=failed"))

    assert "dXNlcjpwYXNz" not in output
    assert output.endswith("upstream rejected Authorization: [REDACTED]\n")
    assert "status=failed" not in output


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_authorization_redaction_does_not_swallow_following_traceback_line(monkeypatch, log_format):
    import sys

    formatter = _formatter_from_config(monkeypatch, log_format)
    basic = "BASICCRED"
    keyed = "KEYEDCRED"
    try:
        raise RuntimeError("authorization=Basic " + basic + "\nstatus=failed\napi_key=" + keyed)
    except RuntimeError:
        record = _record("request failed", exc_info=sys.exc_info())

    output = _render(formatter, record)
    rendered = json.loads(output)["exception"] if log_format == "json" else output

    assert basic not in rendered
    assert "authorization=[REDACTED]" in rendered
    assert "status=failed" in rendered
    assert keyed not in rendered
    assert "api_key=[REDACTED]" in rendered


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_unterminated_json_secret_with_trailing_escape_redacts_through_end_of_line(monkeypatch, log_format):
    import sys

    formatter = _formatter_from_config(monkeypatch, log_format)
    token = "QAJSONSECRET"
    try:
        raise RuntimeError('payload={"token":"' + token + "\\\nsafe diagnostic line")
    except RuntimeError:
        record = _record("request failed", exc_info=sys.exc_info())

    output = _render(formatter, record)
    rendered = json.loads(output)["exception"] if log_format == "json" else output

    assert token not in rendered
    assert '{"token":"[REDACTED]\nsafe diagnostic line' in rendered


def test_basic_token_prepass_does_not_swallow_following_line():
    output = runtime_logging.redact_rendered_log_text("Authorization: Basic \nSAFE diagnostic", keyed_secrets=False)

    assert output == "Authorization: Basic \nSAFE diagnostic"


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_unterminated_json_secret_redacts_through_end_of_line(monkeypatch, log_format):
    import sys

    formatter = _formatter_from_config(monkeypatch, log_format)
    token = "QAJSONSECRET"
    try:
        raise RuntimeError('payload={"token":"' + token + "\nsafe diagnostic line")
    except RuntimeError:
        record = _record("request failed", exc_info=sys.exc_info())

    output = _render(formatter, record)
    rendered = json.loads(output)["exception"] if log_format == "json" else output

    assert token not in rendered
    assert '{"token":"[REDACTED]' in rendered
    assert "safe diagnostic line" in rendered


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_bearer_redaction_consumes_glued_colon_tail(monkeypatch, log_format):
    formatter = _formatter_from_config(monkeypatch, log_format)
    output = _render(formatter, _record("upstream Bearer abc.def:GLUEDTAIL, status=502"))
    rendered = json.loads(output)["message"] if log_format == "json" else output

    assert "abc.def" not in rendered
    assert "GLUEDTAIL" not in rendered
    assert "Bearer [REDACTED], status=502" in rendered


def test_secret_pattern_redaction_preserves_terminators_and_is_idempotent():
    text = (
        "authorization=Digest username=a\n"
        "retrying upstream\r\n"
        "cr-only diagnostic\r"
        'payload={"token":"QAJSONSECRET\n'
        "Bearer abc.def:GLUEDTAIL, status=502\n"
    )
    terminators = re.compile(r"\r\n|\n|\r")

    once = runtime_logging.redact_rendered_log_text(text)
    twice = runtime_logging.redact_rendered_log_text(once)

    assert once == twice
    assert terminators.findall(once) == terminators.findall(text)
    assert "retrying upstream" in once
    assert "cr-only diagnostic" in once
    assert "abc.def" not in once
    assert "GLUEDTAIL" not in once
    assert "QAJSONSECRET" not in once


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_bootstrap_token_output_is_byte_identical_through_redaction(monkeypatch, log_format):
    from app.core.bootstrap import log_bootstrap_token

    formatter = _formatter_from_config(monkeypatch, log_format)
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("tests.bootstrap_token_redaction")
    logger.propagate = False
    logger.addHandler(handler := _Capture())
    try:
        log_bootstrap_token(logger, "bt_0123456789abcdefTOKENVALUE")
    finally:
        logger.removeHandler(handler)
    (record,) = records

    redacted_output = _render(formatter, record)
    monkeypatch.setattr(runtime_logging, "redact_rendered_log_text", lambda text, **kwargs: text)
    plain_output = _render(formatter, record)

    assert "bt_0123456789abcdefTOKENVALUE" in redacted_output
    if log_format == "json":
        # JsonFormatter stamps datetime.now() per call; compare everything else.
        redacted_entry, plain_entry = (json.loads(output) for output in (redacted_output, plain_output))
        assert redacted_entry.pop("timestamp") and plain_entry.pop("timestamp")
        assert redacted_entry == plain_entry
    else:
        assert redacted_output == plain_output


def test_json_formatter_redacts_extras_and_nested_values(json_formatter):
    record = _record("upstream failure", name="app.core.clients.codex")
    record.proxy_url = runtime_basic_auth_url("u", "EXTRAPW", "proxy.test:1")
    record.details = {"urls": [runtime_basic_auth_url("u", "NESTEDPW", "proxy.test:2")], "password": "PLAINPW"}

    class _Unserializable:
        def __repr__(self) -> str:
            return "<Conn " + runtime_basic_auth_url("u", "REPRPW", "proxy.test:3") + ">"

    record.connection = _Unserializable()

    parsed = json.loads(json_formatter.format(record))

    assert parsed["proxy_url"] == "http://[REDACTED]@proxy.test:1"
    assert parsed["details"]["urls"] == ["http://[REDACTED]@proxy.test:2"]
    assert parsed["details"]["password"] == "[REDACTED]"
    assert parsed["connection"] == "<Conn http://[REDACTED]@proxy.test:3>"
    for secret in ("EXTRAPW", "NESTEDPW", "REPRPW", "PLAINPW"):
        assert secret not in json.dumps(parsed)


@pytest.mark.parametrize("level", [logging.WARNING, logging.ERROR])
def test_json_formatter_redacts_secret_keyed_extras_at_warning_and_above(json_formatter, level):
    record = _record("proxy auth failed", level=level, name="app.core.clients.codex")
    record.api_key = "TOPLEVELKEY"
    record.details = {"access_token": "NESTEDTOKEN", "attempt": 2, "tokens": "not-a-secret"}

    parsed = json.loads(json_formatter.format(record))

    assert parsed["api_key"] == "[REDACTED]"
    assert parsed["details"] == {"access_token": "[REDACTED]", "attempt": 2, "tokens": "not-a-secret"}


def test_json_formatter_leaves_secret_keyed_extras_alone_below_warning(json_formatter):
    # INFO extras keep the cheap value-only pass (same trade-off as text records).
    record = _record("usage refreshed", level=logging.INFO, name="app.core.usage")
    record.details = {"token_count": "12", "password": "info-level"}

    parsed = json.loads(json_formatter.format(record))

    assert parsed["details"] == {"token_count": "12", "password": "info-level"}


def test_json_access_formatter_redacts_request_line():
    record = _record("", level=logging.INFO, name="uvicorn.access")
    record.client_addr = "10.0.0.5:1234"
    record.request_line = "GET /probe?target=" + runtime_basic_auth_url("u", "ACCESSPW", "h") + " HTTP/1.1"
    record.status_code = 200

    parsed = json.loads(JsonAccessFormatter().format(record))

    assert "ACCESSPW" not in parsed["request"]
    assert parsed["request"] == "GET /probe?target=http://[REDACTED]@h HTTP/1.1"
    assert parsed["client"] == "10.0.0.5:1234"


def test_text_access_formatter_redacts_request_line():
    formatter = UtcAccessFormatter(
        fmt='%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        use_colors=None,
    )
    record = _record('%s - "%s %s HTTP/%s" %d', level=logging.INFO, name="uvicorn.access")
    record.args = ("10.0.0.5:1234", "GET", "/probe?target=" + runtime_basic_auth_url("u", "ACCESSPW", "h"), "1.1", 200)

    output = formatter.format(record)

    assert "ACCESSPW" not in output
    assert "http://[REDACTED]@h" in output


def test_redact_log_value_masks_url_userinfo():
    value = "proxy " + runtime_basic_auth_url("user", "secret-pw", "proxy.test:8080") + "/path failed"

    assert _redact_log_value(value) == "proxy http://[REDACTED]@proxy.test:8080/path failed"


def test_redact_rendered_log_text_leaves_plain_email_addresses_alone():
    line = "notify owner ops@example.com about https://status.example.com/incident"

    assert runtime_logging.redact_rendered_log_text(line) == line


@pytest.mark.parametrize(
    "line",
    [
        "GET https://status.example?owner=ops@example.com done",
        "see https://status.example#frag@x",
        "https://host/path?owner=ops@example.com",
    ],
)
def test_redact_rendered_log_text_leaves_host_only_urls_with_at_in_query_or_fragment_alone(line):
    # RFC 3986 userinfo cannot contain ``?`` or ``#``; a bare host followed by
    # a query/fragment holding an e-mail address is not a credential.
    assert runtime_logging.redact_rendered_log_text(line) == line


def test_redact_rendered_log_text_still_masks_userinfo_before_query():
    line = "probe " + runtime_basic_auth_url("u", "QUERYPW", "h") + "?next=ops@example.com"

    assert runtime_logging.redact_rendered_log_text(line) == "probe http://[REDACTED]@h?next=ops@example.com"


def _client_http_proxy_error(password: str, loop=None):
    # Exact aiohttp shape for a rejected CONNECT: the tunnel request's headers
    # (our Proxy-Authorization) ride in request_info and are rendered by
    # repr(exc); str(exc) and tracebacks stay credential-free.
    import asyncio

    import aiohttp
    from aiohttp.client_reqrep import ClientRequest
    from yarl import URL

    from app.core.upstream_proxy import ResolvedProxyEndpoint

    endpoint = ResolvedProxyEndpoint("ep", "https", "proxy.test", 8080, "smart-user", password)
    proxy_headers = endpoint.aiohttp_proxy_kwargs()["proxy_headers"]
    owned_loop = loop is None
    loop = loop or asyncio.new_event_loop()
    try:
        request = ClientRequest("CONNECT", URL("https://chatgpt.com/"), headers=proxy_headers, loop=loop)
    finally:
        if owned_loop:
            loop.close()
    return aiohttp.ClientHttpProxyError(request.request_info, (), status=502, message="nope")


def _basic_token(username: str, password: str) -> str:
    import base64

    return base64.b64encode(f"{username}:{password}".encode()).decode()


@pytest.mark.parametrize("log_format", ["text", "json"])
@pytest.mark.parametrize("level", [logging.INFO, logging.ERROR])
def test_rendered_client_http_proxy_error_repr_redacts_basic_token(monkeypatch, log_format, level):
    exc = _client_http_proxy_error("SECRETPW")
    token = _basic_token("smart-user", "SECRETPW")
    assert token in repr(exc)
    assert token not in str(exc)
    record = logging.LogRecord("aiohttp.client", level, "connector.py", 1, "proxy failure: %r", (exc,), None)

    output = _render(_formatter_from_config(monkeypatch, log_format), record)

    assert token not in output
    assert "SECRETPW" not in output
    assert "'Proxy-Authorization': 'Basic [REDACTED]'" in output
    assert "real_url=URL('https://chatgpt.com/')" in output


def test_redact_log_value_masks_basic_token_in_proxy_error_repr():
    exc = _client_http_proxy_error("SECRETPW")

    redacted = _redact_log_value(repr(exc))

    assert redacted is not None
    assert _basic_token("smart-user", "SECRETPW") not in redacted
    assert "'Basic [REDACTED]'" in redacted


@pytest.mark.parametrize("scheme", ["Basic", "basic", "BASIC"])
def test_basic_token_redaction_without_keyed_secrets_covers_common_scheme_spellings(scheme):
    token = _basic_token("user", "pass")
    text = f"headers ('Proxy-Authorization': '{scheme} {token}')"

    # INFO-level records (keyed_secrets=False) still mask the canonical,
    # lowercase and uppercase scheme spellings via the substring prechecks.
    assert runtime_logging.redact_rendered_log_text(text, keyed_secrets=False) == (
        f"headers ('Proxy-Authorization': '{scheme} [REDACTED]')"
    )
    assert token not in runtime_logging.redact_rendered_log_text(text)


def test_basic_token_redaction_without_keyed_secrets_skips_mixed_case_scheme():
    token = _basic_token("user", "pass")
    mixed = f"headers ('Proxy-Authorization': 'BaSiC {token}')"

    # Deliberate hot-path trade-off: INFO-level records only pay for the three
    # substring prechecks, so exotic mixed-case spellings are left to the
    # WARNING-and-higher keyed pass (which runs the case-insensitive regex).
    assert runtime_logging.redact_rendered_log_text(mixed, keyed_secrets=False) == mixed
    assert runtime_logging.redact_rendered_log_text(mixed) == "headers ('Proxy-Authorization': 'BaSiC [REDACTED]')"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(["PW1", "PW2"], id="list"),
        pytest.param(123456, id="int"),
        pytest.param(b"PWBYTES", id="bytes"),
        pytest.param({"nested": "PWNESTED"}, id="dict"),
    ],
)
def test_json_formatter_redacts_non_string_values_under_secret_keys(json_formatter, value):
    record = _record("proxy auth failed", level=logging.WARNING, name="app.core.clients.codex")
    record.password = value
    record.details = {"token": value, "attempt": 3}

    parsed = json.loads(json_formatter.format(record))

    assert parsed["password"] == "[REDACTED]"
    assert parsed["details"] == {"token": "[REDACTED]", "attempt": 3}
    assert "PW" not in json.dumps(parsed).replace("[REDACTED]", "")


def test_json_formatter_keeps_null_under_secret_keys(json_formatter):
    record = _record("proxy auth failed", level=logging.WARNING, name="app.core.clients.codex")
    record.password = None

    parsed = json.loads(json_formatter.format(record))

    assert parsed["password"] is None


def test_json_formatter_redacts_userinfo_in_extra_keys(json_formatter):
    # Keys are rendered too; the cheap userinfo pass applies at every level.
    record = _record("proxy failure", level=logging.INFO, name="app.core.clients.codex")
    record.details = {runtime_basic_auth_url("u", "PWKEY", "proxy.test:1"): "failed"}
    record.__dict__[runtime_basic_auth_url("u", "PWTOP", "proxy.test:2")] = "failed"

    parsed = json.loads(json_formatter.format(record))

    assert parsed["details"] == {"http://[REDACTED]@proxy.test:1": "failed"}
    assert parsed["http://[REDACTED]@proxy.test:2"] == "failed"
    assert "PWKEY" not in json.dumps(parsed)
    assert "PWTOP" not in json.dumps(parsed)


# --- never-raise: cyclic, deep and unprintable structured extras -------------


class _ExplodingRepr:
    def __repr__(self) -> str:
        raise RuntimeError("repr failure")


def test_json_formatter_emits_record_with_self_referential_dict_extra(json_formatter):
    # Regression: the key-aware rebuild recursed on cycles and raised
    # RecursionError ahead of the json.dumps guard, so the record was dropped.
    details: dict[str, object] = {"password": "CYCLEPW", "proxy_url": runtime_basic_auth_url("u", "CYCLEURLPW", "h")}
    details["self"] = details
    record = _record("proxy auth failed", level=logging.WARNING, name="app.core.clients.codex")
    record.details = details

    output = _render(json_formatter, record)

    assert output, "the record must be emitted"
    parsed = json.loads(output)
    assert parsed["message"] == "proxy auth failed"
    assert parsed["details"] == {"password": "[REDACTED]", "proxy_url": "http://[REDACTED]@h", "self": "{...}"}
    assert "CYCLEPW" not in output
    assert "CYCLEURLPW" not in output


def test_json_formatter_emits_record_with_list_cycle_extra(json_formatter):
    urls: list[object] = [runtime_basic_auth_url("u", "LISTPW", "proxy.test:2")]
    urls.append(urls)
    record = _record("proxy failure", level=logging.INFO, name="app.core.clients.codex")
    record.details = {"urls": urls, "attempt": 1}

    output = _render(json_formatter, record)

    assert output, "the record must be emitted"
    parsed = json.loads(output)
    assert parsed["details"] == {"urls": ["http://[REDACTED]@proxy.test:2", "[...]"], "attempt": 1}
    assert "LISTPW" not in output


def test_json_formatter_renders_over_deep_extras_as_redacted_text(json_formatter):
    leaf: object = runtime_basic_auth_url("u", "DEEPPW", "h")
    for _ in range(200):
        leaf = [leaf]
    record = _record("proxy failure", level=logging.INFO, name="app.core.clients.codex")
    record.details = leaf

    output = _render(json_formatter, record)

    assert output, "the record must be emitted"
    parsed = json.loads(output)
    assert "DEEPPW" not in output
    assert "http://[REDACTED]@h" in json.dumps(parsed["details"])


def test_json_formatter_redacts_secret_keyed_leaf_below_depth_limit(json_formatter):
    # Beyond ``_MAX_JSON_LOG_DEPTH`` the subtree is rendered as Python repr
    # text; a secret-keyed mapping there must still be masked at WARNING+.
    leaf: object = {"password": "DEEPKEYEDPW", "u": runtime_basic_auth_url("u", "DEEPURLPW", "h")}
    for _ in range(runtime_logging._MAX_JSON_LOG_DEPTH + 8):
        leaf = {"n": leaf}
    record = _record("proxy auth failed", level=logging.WARNING, name="app.core.clients.codex")
    record.details = leaf

    output = _render(json_formatter, record)

    assert output, "the record must be emitted"
    assert json.loads(output)["message"] == "proxy auth failed"
    assert "DEEPKEYEDPW" not in output
    assert "DEEPURLPW" not in output
    assert "'password': '[REDACTED]'" in output
    assert "http://[REDACTED]@h" in output


def test_json_formatter_emits_record_when_extra_repr_explodes(json_formatter):
    record = _record("proxy auth failed", level=logging.WARNING, name="app.core.clients.codex")
    record.details = {"password": "EXPLODEPW", "connection": _ExplodingRepr()}
    record.connection = _ExplodingRepr()

    output = _render(json_formatter, record)

    assert output, "the record must be emitted"
    parsed = json.loads(output)
    assert parsed["message"] == "proxy auth failed"
    assert parsed["details"] == "<unprintable dict>"
    assert parsed["connection"] == "<unprintable _ExplodingRepr>"
    assert "EXPLODEPW" not in output


def test_json_formatter_emits_record_when_extra_iteration_explodes(json_formatter):
    class _ExplodingItems(dict):
        def items(self):
            raise RuntimeError("iteration failure")

    record = _record("proxy auth failed", level=logging.WARNING, name="app.core.clients.codex")
    record.details = _ExplodingItems(attempt=1, password="ITEMSPW")

    output = _render(json_formatter, record)

    assert output, "the record must be emitted"
    parsed = json.loads(output)
    assert parsed["message"] == "proxy auth failed"
    # dict.__repr__ bypasses the exploding items(); the text fallback masks the key.
    assert "ITEMSPW" not in output
    assert parsed["details"] == "{'attempt': 1, 'password': '[REDACTED]'}"


def _fuzz_extra(rng, depth: int, containers: list[object]) -> object:
    kind = rng.randrange(12)
    if depth <= 0 or kind < 4:
        return rng.choice(
            [
                "plain",
                runtime_basic_auth_url("u", "FUZZPW", "h"),
                b"FUZZBYTES",
                42,
                1.5,
                None,
                True,
                _ExplodingRepr(),
                "password=FUZZKEYED",
            ]
        )
    if kind < 6 and containers:
        return rng.choice(containers)  # back-reference: cycle or shared subtree
    size = rng.randrange(4)
    if kind < 8:
        built: object = tuple(_fuzz_extra(rng, depth - 1, containers) for _ in range(size))
    elif kind < 10:
        items = [_fuzz_extra(rng, depth - 1, containers) for _ in range(size)]
        built = items
        containers.append(items)
        if rng.random() < 0.3:
            items.append(items)
    else:
        keys = ["password", "token", "attempt", "proxy_url", 7, runtime_basic_auth_url("u", "FUZZKEYPW", "h")]
        mapping = {rng.choice(keys): _fuzz_extra(rng, depth - 1, containers) for _ in range(size)}
        built = mapping
        containers.append(mapping)
        if rng.random() < 0.3:
            mapping["self"] = mapping
    return built


@pytest.mark.parametrize("level", [logging.INFO, logging.ERROR])
def test_json_formatter_never_raises_on_random_nested_extras(json_formatter, level):
    import random

    for seed in range(150):
        rng = random.Random(seed)
        record = _record("fuzz %s", level=level, name="app.core.clients.codex")
        record.args = (seed,)
        record.details = _fuzz_extra(rng, depth=5, containers=[])
        record.leaf = _fuzz_extra(rng, depth=1, containers=[])

        output = _render(json_formatter, record)

        assert output, f"seed {seed}: the record must be emitted"
        parsed = json.loads(output)
        assert parsed["message"] == f"fuzz {seed}"
        assert "details" in parsed and "leaf" in parsed
