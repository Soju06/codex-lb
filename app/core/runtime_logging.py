from __future__ import annotations

import copy
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from types import TracebackType
from typing import cast

from fastapi import Request
from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter, DefaultFormatter

from app.core.types import JsonValue
from app.core.utils.request_id import get_request_id

_LOG_REDACTION = "[REDACTED]"
# Bounded by whitespace and structural delimiters rather than by the token68
# alphabet so a malformed credential cannot leave a glued suffix like
# `abc?secret`, while a closing quote or bracket after a token is kept.
_BEARER_CREDENTIAL_CHARACTER = r"""[^\s,&;"'\\)\]}]"""
_SENSITIVE_LOG_VALUE_PATTERNS = (
    re.compile(r"(?i)(password|passwd|pwd|token|secret|api[_-]?key)(\s*[=:]\s*)([^\s,&]+)"),
    # An earlier marker is consumed whole so re-formatting is idempotent.
    re.compile(
        rf"(?i)(bearer\s+)(?:{re.escape(_LOG_REDACTION)}{_BEARER_CREDENTIAL_CHARACTER}*|{_BEARER_CREDENTIAL_CHARACTER}+)"
    ),
    re.compile(r"(?i)(authorization\s*[=:]\s*)(?!\s*bearer\b)([^,&]+)"),
)
# A value cut off by the end of its line (traceback lines are redacted one at a
# time) is redacted through that line end, including a dangling escape.
_JSON_SENSITIVE_LOG_VALUE_PATTERN = re.compile(
    r'(?i)("(?:password|passwd|pwd|token|secret|api[_-]?key|authorization)"\s*:\s*")'
    r'(?:\\.|[^"\\])*\\?("|$)'
)
# Every boundary str.splitlines() honors, so per-line redaction never spans one.
_LINE_TERMINATORS = "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029"

type _ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None] | tuple[None, None, None]


def _redact_log_value(value: str | None) -> str | None:
    collapsed = _collapse_log_value(value)
    if collapsed is None:
        return None
    return _redact_sensitive_log_line(collapsed)


def _redact_sensitive_log_line(line: str) -> str:
    redacted = _JSON_SENSITIVE_LOG_VALUE_PATTERN.sub(_redact_json_secret, line)
    redacted = _SENSITIVE_LOG_VALUE_PATTERNS[0].sub(_redact_keyed_secret, redacted)
    redacted = _SENSITIVE_LOG_VALUE_PATTERNS[1].sub(_redact_bearer_token, redacted)
    return _SENSITIVE_LOG_VALUE_PATTERNS[2].sub(_redact_authorization_value, redacted)


def _redact_traceback_text(text: str) -> str:
    # Apply the one-line policy to each line on its own so no pattern can
    # consume a line terminator; frames and the exception line stay intact.
    parts: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip(_LINE_TERMINATORS)
        parts.append(_redact_sensitive_log_line(body) + line[len(body) :])
    return "".join(parts)


def _redact_keyed_secret(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}{_LOG_REDACTION}"


def _redact_json_secret(match: re.Match[str]) -> str:
    return f"{match.group(1)}{_LOG_REDACTION}{match.group(2)}"


def _redact_bearer_token(match: re.Match[str]) -> str:
    return f"{match.group(1)}{_LOG_REDACTION}"


def _redact_authorization_value(match: re.Match[str]) -> str:
    return f"{match.group(1)}{_LOG_REDACTION}"


def _utc_converter(seconds: float | None) -> time.struct_time:
    return time.gmtime(seconds)


class UtcDefaultFormatter(DefaultFormatter):
    converter: Callable[[float | None], time.struct_time] = staticmethod(_utc_converter)

    def format(self, record: logging.LogRecord) -> str:
        # logging.Formatter.format() caches the traceback on the shared record as
        # exc_text and appends another formatter's cache verbatim. Format a copy
        # so this formatter neither emits a cached unredacted traceback nor
        # changes what other handlers see.
        record = copy.copy(record)
        if record.exc_info:
            record.exc_text = None
        elif record.exc_text:
            record.exc_text = _redact_traceback_text(record.exc_text)
        return super().format(record)

    def formatException(self, ei: _ExcInfo) -> str:
        return _redact_traceback_text(super().formatException(ei))


class UtcAccessFormatter(AccessFormatter):
    converter: Callable[[float | None], time.struct_time] = staticmethod(_utc_converter)


class JsonFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__()

    def formatException(self, ei: _ExcInfo) -> str:
        return _redact_traceback_text(super().formatException(ei))

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
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
            if key not in excluded_keys:
                try:
                    json.dumps(value)
                    log_entry[key] = value
                except (TypeError, ValueError):
                    log_entry[key] = str(value)

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


class JsonAccessFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, JsonValue] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "type": "access",
            "client": getattr(record, "client_addr", None),
            "request": getattr(record, "request_line", None),
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
