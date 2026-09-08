from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import Request
from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter, DefaultFormatter

from app.core.types import JsonValue
from app.core.utils.request_id import get_request_id

_SENSITIVE_LOG_VALUE_PATTERNS = (
    re.compile(r"(?i)(password|passwd|pwd|token|secret|api[_-]?key)(\s*[=:]\s*)([^\s,&]+)"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=:-]+"),
    re.compile(r"(?i)(authorization\s*[=:]\s*)(?!\s*bearer\b)([^,&]+)"),
)
_LINE_BREAKS = re.compile(r"(\r\n|\n|\r)")
# RFC 7617 ``Basic <base64>`` token: a reversible encoding of ``user:password``
# that aiohttp reprs verbatim (``ClientHttpProxyError.request_info.headers``
# carries the CONNECT ``Proxy-Authorization`` header). Applied to every record
# behind cheap substring scans for the canonical, lowercase and uppercase
# scheme spellings (the auth-scheme is case-insensitive per RFC 7235); the
# regex itself is case-insensitive once a precheck hits.
_BASIC_TOKEN_PATTERN = re.compile(r"(?i)(basic\s+)[A-Za-z0-9+/=]+")
_BASIC_TOKEN_PRECHECKS = ("Basic ", "basic ", "BASIC ")
# Fail-closed rendering when a redaction pass itself raises: the record is
# still emitted (timestamp/level/logger intact) but never with the original,
# possibly credential-bearing text.
_REDACTION_FAILED_PLACEHOLDER = "[REDACTED: log redaction failed]"
_JSON_SENSITIVE_LOG_VALUE_PATTERN = re.compile(
    r'(?i)("(?:password|passwd|pwd|token|secret|api[_-]?key|authorization)"\s*:\s*")'
    r'(?:\\.|[^"\\])*(?:\\(?=\Z))?("|(?=\Z))'
)
# ``scheme://user:pass@`` userinfo, e.g. aiohttp ConnectionKey proxy URL reprs.
# RFC 3986 userinfo never contains ``/``, ``?`` or ``#``, so a bare host
# followed by a query/fragment holding an ``@`` is left alone. ``'`` is an
# RFC 3986 sub-delim that yarl leaves unencoded in userinfo (``URL('http://u:p'w@h')``),
# so it must stay matchable; ``"`` is not valid unencoded userinfo (yarl emits
# ``%22``) and stays excluded so a compact JSON document holding a URL and an
# e-mail address is not over-matched.
_USERINFO_PATTERN = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/?#\s@\"]+)@")
# Python ``repr()`` of a secret-keyed mapping, ``{'password': 'x', 'token': 1}``:
# the quote between key and colon defeats the ``key=value`` pattern above and
# the JSON pattern below is double-quote only. Reached by ``%r``-logged
# mappings and by the JSON formatter whenever a structured extra has to be
# rendered as text (depth limit, exploding iteration, unserializable rebuild).
# The key matches on suffix like ``_SENSITIVE_LOG_KEY_PATTERN``; the value is
# a quoted string (quotes kept), bytes, a flat container, or a bare token up to
# the next separator. Containers nested under a secret key are masked only to
# their first level: a best-effort text backstop, not the structural pass.
_PYTHON_REPR_SENSITIVE_LOG_VALUE_PATTERN = re.compile(
    r"(?i)('[^'\\]*(?:password|passwd|pwd|token|secret|api[_-]?key|authorization)'\s*:\s*)"
    r"(b?'(?:\\.|[^'\\])*'|b?\"(?:\\.|[^\"\\])*\"|\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}|[^,}\]\)\s]+)"
)
# Structured (JSON extra) keys whose string values are secrets by name.
_SENSITIVE_LOG_KEY_PATTERN = re.compile(r"(?i)(password|passwd|pwd|token|secret|api[_-]?key|authorization)$")
# Case-folded substrings that must be present before the keyed/bearer/
# authorization/JSON patterns above can match; keeps the per-record cost of
# credential-free lines to a casefold plus substring scans.
_SECRET_HINTS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "api_key",
    "api-key",
    "apikey",
    "bearer",
    "basic",
    "authorization",
)
_LOG_REDACTION = "[REDACTED]"


def _redact_log_value(value: str | None) -> str | None:
    collapsed = _collapse_log_value(value)
    if collapsed is None:
        return None
    return _redact_secret_patterns(_USERINFO_PATTERN.sub(_redact_userinfo, collapsed))


def _redact_secret_patterns_on_line(text: str) -> str:
    redacted = _JSON_SENSITIVE_LOG_VALUE_PATTERN.sub(_redact_json_secret, text)
    redacted = _SENSITIVE_LOG_VALUE_PATTERNS[0].sub(_redact_keyed_secret, redacted)
    redacted = _SENSITIVE_LOG_VALUE_PATTERNS[1].sub(_redact_bearer_token, redacted)
    redacted = _BASIC_TOKEN_PATTERN.sub(_redact_bearer_token, redacted)
    redacted = _SENSITIVE_LOG_VALUE_PATTERNS[2].sub(_redact_authorization_value, redacted)
    # Last, so ``'Proxy-Authorization': 'Basic [REDACTED]'`` keeps the scheme
    # the token passes above already exposed.
    return _PYTHON_REPR_SENSITIVE_LOG_VALUE_PATTERN.sub(_redact_python_repr_secret, redacted)


def _map_log_lines(text: str, transform: Callable[[str], str]) -> str:
    if "\n" not in text and "\r" not in text:
        return transform(text)
    return "".join(transform(part) if index % 2 == 0 else part for index, part in enumerate(_LINE_BREAKS.split(text)))


def _redact_secret_patterns(text: str) -> str:
    return _map_log_lines(text, _redact_secret_patterns_on_line)


def _redact_basic_tokens_on_line(text: str) -> str:
    return _BASIC_TOKEN_PATTERN.sub(_redact_bearer_token, text)


def redact_rendered_log_text(text: str, *, keyed_secrets: bool = True) -> str:
    """Mask URL userinfo, Basic tokens and, optionally, keyed secrets in a rendered log string.

    Applied to every rendered record regardless of the originating logger
    (asyncio, aiohttp, uvicorn, tracebacks). ``keyed_secrets=False`` limits the
    pass to the substring-precheck patterns (URL userinfo and ``Basic <token>``
    in its ``Basic``/``basic``/``BASIC`` spellings); formatters use it for INFO and lower records because the
    keyed patterns cost tens of microseconds on long hot-path lines. Never
    raises: if a pass fails the text is replaced with a fail-closed placeholder
    so logging itself cannot break and the original text is never emitted.
    """
    try:
        redacted = text
        if "@" in text and "://" in text:
            redacted = _USERINFO_PATTERN.sub(_redact_userinfo, redacted)
        if any(precheck in text for precheck in _BASIC_TOKEN_PRECHECKS):
            redacted = _map_log_lines(redacted, _redact_basic_tokens_on_line)
        if not keyed_secrets:
            return redacted
        folded = text.casefold()
        for hint in _SECRET_HINTS:
            if hint in folded:
                return _redact_secret_patterns(redacted)
        return redacted
    except Exception:
        return _REDACTION_FAILED_PLACEHOLDER


def _redact_record_text(record: logging.LogRecord, text: str) -> str:
    return redact_rendered_log_text(text, keyed_secrets=record.levelno >= logging.WARNING)


def _redact_userinfo(match: re.Match[str]) -> str:
    return f"{match.group(1)}{_LOG_REDACTION}@"


def _redact_keyed_secret(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}{_LOG_REDACTION}"


def _redact_json_secret(match: re.Match[str]) -> str:
    closer = match.group(2) or ""
    return f"{match.group(1)}{_LOG_REDACTION}{closer}"


def _redact_python_repr_secret(match: re.Match[str]) -> str:
    value = match.group(2)
    if _LOG_REDACTION in value:
        return match.group(0)
    quote = value[0] if value[0] in "'\"" else ""
    return f"{match.group(1)}{quote}{_LOG_REDACTION}{quote}"


def _redact_bearer_token(match: re.Match[str]) -> str:
    return f"{match.group(1)}{_LOG_REDACTION}"


def _redact_authorization_value(match: re.Match[str]) -> str:
    return f"{match.group(1)}{_LOG_REDACTION}"


def _utc_converter(seconds: float | None) -> time.struct_time:
    return time.gmtime(seconds)


class _RedactingFormatterMixin(logging.Formatter):
    """Redact the fully rendered record (message, exception text, stack info)."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = _redact_record_text(record, super().format(record))
        if rendered is _REDACTION_FAILED_PLACEHOLDER:
            # Fail closed but keep a secret-free prefix (time, level, logger)
            # so the line still says when and where it came from.
            return f"{self.formatTime(record, self.datefmt)} {record.levelname} {record.name} {rendered}"
        return rendered


class UtcDefaultFormatter(_RedactingFormatterMixin, DefaultFormatter):
    converter: Callable[[float | None], time.struct_time] = staticmethod(_utc_converter)


class UtcAccessFormatter(_RedactingFormatterMixin, AccessFormatter):
    converter: Callable[[float | None], time.struct_time] = staticmethod(_utc_converter)


# Containers nested deeper than this are rendered as redacted text instead of
# being rebuilt, so the key-aware pass can never recurse without bound.
_MAX_JSON_LOG_DEPTH = 32
# Placeholders for a container that refers back to an ancestor, mirroring the
# ``{...}`` / ``[...]`` markers Python's own repr emits for recursive objects.
_CYCLIC_JSON_LOG_PLACEHOLDERS: dict[type, str] = {dict: "{...}", list: "[...]", tuple: "(...)"}


def _redact_json_log_value(
    record: logging.LogRecord,
    value: object,
    *,
    _ancestors: frozenset[int] = frozenset(),
    _depth: int = 0,
) -> object:
    """Rebuild ``value`` with redaction applied to every reachable string (and secret-keyed field).

    Cycles (a container that refers back to an ancestor) collapse to a repr
    placeholder and nesting beyond ``_MAX_JSON_LOG_DEPTH`` is rendered as
    redacted text, so redaction still applies wherever the structure is finite
    and the rebuild itself cannot raise ``RecursionError``.
    """
    if isinstance(value, str):
        return _redact_record_text(record, value)
    if not isinstance(value, (dict, list, tuple)):
        return value
    if id(value) in _ancestors:
        return next(marker for kind, marker in _CYCLIC_JSON_LOG_PLACEHOLDERS.items() if isinstance(value, kind))
    if _depth >= _MAX_JSON_LOG_DEPTH:
        return _redact_record_text(record, _safe_str(value))
    ancestors = _ancestors | {id(value)}
    depth = _depth + 1
    if isinstance(value, dict):
        return {
            _redact_json_log_key(record, key): _redact_json_log_item(record, key, item, ancestors, depth)
            for key, item in value.items()
        }
    return [_redact_json_log_value(record, item, _ancestors=ancestors, _depth=depth) for item in value]


def _safe_str(value: object) -> str:
    # ``str()`` of an extra can itself raise (exploding ``__repr__``, repr of a
    # pathologically deep container); logging must still emit the record.
    try:
        return str(value)
    except Exception:
        return f"<unprintable {type(value).__name__}>"


def _redact_json_log_key(record: logging.LogRecord, key: object) -> object:
    # Keys are rendered too (``{"https://u:pw@proxy": "failed"}``).
    return _redact_record_text(record, key) if isinstance(key, str) else key


def _is_secret_json_log_key(record: logging.LogRecord, key: object) -> bool:
    # Key-aware for WARNING+ like the keyed text patterns: ``{"password": x}``
    # carries the secret in a value the value-only pass cannot recognise,
    # whatever its type (string, list, number, bytes).
    return record.levelno >= logging.WARNING and isinstance(key, str) and bool(_SENSITIVE_LOG_KEY_PATTERN.search(key))


def _redact_json_log_item(
    record: logging.LogRecord, key: object, value: object, ancestors: frozenset[int], depth: int
) -> object:
    if value is not None and _is_secret_json_log_key(record, key):
        return _LOG_REDACTION
    return _redact_json_log_value(record, value, _ancestors=ancestors, _depth=depth)


class JsonFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_record_text(record, record.getMessage()),
        }

        try:
            from app.core.tracing.otel import get_current_span_id, get_current_trace_id

            trace_id = get_current_trace_id()
            span_id = get_current_span_id()
            if trace_id:
                log_entry["trace_id"] = trace_id
            if span_id:
                log_entry["span_id"] = span_id
        except Exception:
            pass

        excluded_keys = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "taskName",
        }

        for key, value in record.__dict__.items():
            if key in excluded_keys:
                continue
            safe_key = str(_redact_json_log_key(record, key))
            if value is not None and _is_secret_json_log_key(record, key):
                log_entry[safe_key] = _LOG_REDACTION
                continue
            # Rebuild containers key-aware first so a non-serializable leaf
            # (bytes) cannot drag secret-keyed siblings into the str() fallback.
            # Redaction never raises: if the rebuild fails anyway (a container
            # whose iteration explodes), fall back to the legacy rendering of
            # the original value rather than dropping the record.
            try:
                redacted = _redact_json_log_value(record, value)
            except Exception:
                redacted = value
            try:
                json.dumps(redacted)
                log_entry[safe_key] = redacted
            except Exception:
                # TypeError/ValueError for unserializable leaves and cycles;
                # anything else (RecursionError, exploding iteration) too.
                log_entry[safe_key] = _redact_record_text(record, _safe_str(redacted))

        if record.exc_info:
            log_entry["exception"] = _redact_record_text(record, self.formatException(record.exc_info))

        return json.dumps(log_entry, default=str)


class JsonAccessFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, JsonValue] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "type": "access",
            "client": getattr(record, "client_addr", None),
            "request": cast(JsonValue, _redact_json_log_value(record, getattr(record, "request_line", None))),
            "status": getattr(record, "status_code", None),
        }
        return json.dumps(log_entry, default=str)


type LogConfigValue = str | bool | None | dict[str, "LogConfigValue"]
type LogConfig = dict[str, LogConfigValue]


def build_log_config() -> LogConfig:
    from app.core.config.settings import get_settings

    config = copy.deepcopy(LOGGING_CONFIG)
    formatters = config.setdefault("formatters", {})
    handlers = config.setdefault("handlers", {})
    settings = get_settings()

    if settings.log_format == "json":
        formatters["default"] = {
            "()": "app.core.runtime_logging.JsonFormatter",
        }
    else:
        formatters["default"] = {
            "()": "app.core.runtime_logging.UtcDefaultFormatter",
            "fmt": "%(asctime)s %(levelprefix)s %(name)s %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%SZ",
            "use_colors": None,
        }

    if settings.log_format == "json":
        formatters["access"] = {
            "()": "app.core.runtime_logging.JsonAccessFormatter",
        }
    else:
        formatters["access"] = {
            "()": "app.core.runtime_logging.UtcAccessFormatter",
            "fmt": '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            "datefmt": "%Y-%m-%dT%H:%M:%SZ",
            "use_colors": None,
        }

    # Uvicorn's stock config only wires uvicorn.* loggers. Attach the same
    # default handler to the root logger so application loggers such as
    # app.core.balancer.logic surface in docker logs at INFO.
    handlers.setdefault(
        "default", {"class": "logging.StreamHandler", "formatter": "default", "stream": "ext://sys.stderr"}
    )
    config["root"] = {
        "handlers": ["default"],
        "level": "INFO",
    }
    return cast(LogConfig, config)


class _RedactedRepr:
    """Stand-in whose repr is the redacted rendering of the original object."""

    __slots__ = ("_text",)

    def __init__(self, text: str) -> None:
        self._text = text

    def __repr__(self) -> str:
        return self._text


# Context values the default handler renders as text rather than repr().
_UNREDACTED_LOOP_CONTEXT_KEYS = frozenset({"message", "exception", "source_traceback", "handle_traceback"})
_REDACTING_LOOP_HANDLER_MARKER = "_codex_lb_redacting_loop_handler"
_LOOP_CONTEXT_REDACTION_FAILED = "[REDACTED: loop context redaction failed]"


def _fail_closed_loop_context(context: dict[str, Any]) -> dict[str, Any]:
    """Keep the textual entries and replace every object value with an opaque stand-in."""
    try:
        return {
            key: value if key in _UNREDACTED_LOOP_CONTEXT_KEYS else _RedactedRepr(_LOOP_CONTEXT_REDACTION_FAILED)
            for key, value in context.items()
        }
    except Exception:
        return {"message": _LOOP_CONTEXT_REDACTION_FAILED}


def install_redacting_loop_exception_handler(loop: asyncio.AbstractEventLoop) -> None:
    """Redact credential-bearing object reprs before the loop's default handler logs them.

    The default asyncio/uvloop handler renders every context value with
    ``repr()`` (aiohttp ``Connection<ConnectionKey(... proxy=URL('http://u:pw@host'))>``,
    ``BasicAuth(... password='pw')``) into the ``asyncio`` logger before any
    formatter runs. Idempotent; delegates to the previously installed handler
    (or the default one) so formatting stays byte-identical for contexts that
    contain no secrets. Fails closed: a value whose ``repr()`` raises is
    replaced by an opaque stand-in, and any other failure delegates a context
    whose object values are all stand-ins, so the report is still emitted but
    no unredacted value ever reaches the delegate.
    """
    previous = loop.get_exception_handler()
    if previous is not None and getattr(previous, _REDACTING_LOOP_HANDLER_MARKER, False):
        return

    def _delegate(target_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if previous is None:
            target_loop.default_exception_handler(context)
        else:
            previous(target_loop, context)

    def _handler(target_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        try:
            safe_context = dict(context)
            for key, value in context.items():
                if key in _UNREDACTED_LOOP_CONTEXT_KEYS:
                    continue
                try:
                    rendered = repr(value)
                except Exception as exc:
                    # The default handler would call repr() again and lose the
                    # whole report; an opaque stand-in keeps the message line.
                    safe_context[key] = _RedactedRepr(f"<{type(value).__name__} repr failed: {type(exc).__name__}>")
                    continue
                redacted = redact_rendered_log_text(rendered)
                if redacted != rendered:
                    safe_context[key] = _RedactedRepr(redacted)
        except Exception:
            safe_context = _fail_closed_loop_context(context)
        _delegate(target_loop, safe_context)

    setattr(_handler, _REDACTING_LOOP_HANDLER_MARKER, True)
    loop.set_exception_handler(_handler)


def log_error_response(
    logger: logging.Logger,
    request: Request,
    status_code: int,
    code: str | None,
    message: str | None,
    *,
    category: str,
    exc_info: bool = False,
) -> None:
    level = logging.ERROR if status_code >= 500 else logging.WARNING
    logger.log(
        level,
        "%s request_id=%s method=%s path=%s status=%s code=%s message=%s",
        category,
        get_request_id(),
        request.method,
        request.url.path,
        status_code,
        _error_log_field(code),
        _error_log_field(message),
        exc_info=exc_info,
    )


def _error_log_field(value: str | None) -> str:
    redacted = _redact_log_value(value)
    if redacted is None:
        return "-"
    return json.dumps(redacted)


def _collapse_log_value(value: str | None) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(value.split())
    return collapsed or None
