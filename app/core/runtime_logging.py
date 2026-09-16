from __future__ import annotations

import atexit
import copy
import json
import logging
import logging.config
import logging.handlers
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from fastapi import Request
from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter, DefaultFormatter

from app.core.types import JsonValue
from app.core.utils.request_id import get_request_id

_SENSITIVE_LOG_VALUE_PATTERNS = (
    re.compile(r"(?i)(password|passwd|pwd|token|secret|api[_-]?key)(\s*[=:]\s*)([^\s,&]+)"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(authorization\s*[=:]\s*)(?!\s*bearer\b)([^,&]+)"),
)
_LOG_REDACTION = "[REDACTED]"
_MAX_LOG_VALUE_CHARS = 1000


def _redact_log_value(value: str | None) -> str | None:
    collapsed = _collapse_log_value(value)
    if collapsed is None:
        return None
    redacted = collapsed
    redacted = _SENSITIVE_LOG_VALUE_PATTERNS[0].sub(_redact_keyed_secret, redacted)
    redacted = _SENSITIVE_LOG_VALUE_PATTERNS[1].sub(_redact_bearer_token, redacted)
    redacted = _SENSITIVE_LOG_VALUE_PATTERNS[2].sub(_redact_authorization_value, redacted)
    return _truncate_log_value(redacted)


def _redact_keyed_secret(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}{_LOG_REDACTION}"


def _redact_bearer_token(match: re.Match[str]) -> str:
    return f"{match.group(1)}{_LOG_REDACTION}"


def _redact_authorization_value(match: re.Match[str]) -> str:
    return f"{match.group(1)}{_LOG_REDACTION}"


def _utc_converter(seconds: float | None) -> time.struct_time:
    return time.gmtime(seconds)


class UtcDefaultFormatter(DefaultFormatter):
    converter: Callable[[float | None], time.struct_time] = staticmethod(_utc_converter)


class UtcAccessFormatter(AccessFormatter):
    converter: Callable[[float | None], time.struct_time] = staticmethod(_utc_converter)


class JsonFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__()

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
    _queue_stream_handlers(handlers)
    return cast(LogConfig, config)


# Names of the stream handlers uvicorn's config defines; both write to a
# launchd-redirected file in production.
_QUEUED_HANDLER_NAMES = ("default", "access")
_STREAM_SUFFIX = "_stream"


def _queue_stream_handlers(handlers: dict[str, object]) -> None:
    """Put every stream handler behind a QueueHandler/QueueListener pair.

    ``StreamHandler.emit`` is a blocking ``write`` to stderr/stdout, and under
    launchd those are files on a disk the whole host is contending for. A
    single slow write parks the event loop with every stream in flight
    (sampled mid-stall at ``logging.emit`` on 2026-09-10, 2026-09-12). The
    QueueHandler makes emit a ``queue.put_nowait``; the listener thread does
    the write. ``configure_runtime_logging()`` starts the listeners.
    """
    for name in _QUEUED_HANDLER_NAMES:
        stream = handlers.get(name)
        if not isinstance(stream, dict) or stream.get("class") == "logging.handlers.QueueHandler":
            continue
        stream_name = f"{name}{_STREAM_SUFFIX}"
        handlers[stream_name] = stream
        handlers[name] = {
            "class": "logging.handlers.QueueHandler",
            "handlers": [stream_name],
            "respect_handler_level": True,
        }


def start_log_listeners() -> list[logging.handlers.QueueListener]:
    """Start the QueueListener behind each queued handler (idempotent)."""
    started: list[logging.handlers.QueueListener] = []
    for name in _QUEUED_HANDLER_NAMES:
        handler = logging.getHandlerByName(name)
        listener = getattr(handler, "listener", None)
        if listener is None:
            continue
        if getattr(listener, "_thread", None) is None:
            listener.start()
            atexit.register(_stop_listener, listener)
        started.append(listener)
    return started


def _stop_listener(listener: logging.handlers.QueueListener) -> None:
    try:
        if getattr(listener, "_thread", None) is not None:
            listener.stop()
    except Exception:  # noqa: BLE001 - shutdown must never raise
        pass


def configure_runtime_logging() -> LogConfig:
    """Apply build_log_config() to the process and start its listeners.

    The CLI calls this before uvicorn.run() and passes ``log_config=None`` so
    uvicorn does not re-apply a config whose listeners nobody started.
    """
    config = build_log_config()
    logging.config.dictConfig(cast(dict[str, object], config))
    start_log_listeners()
    return config


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
    request_id = get_request_id()
    safe_message = _redact_log_value(message)
    logger.log(
        level,
        "%s request_id=%s method=%s path=%s status=%s code=%s message=%s",
        category,
        request_id,
        request.method,
        request.url.path,
        status_code,
        code,
        safe_message,
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": status_code,
            "error_code": code,
            "error_message": safe_message,
            "error_category": category,
        },
        exc_info=exc_info,
    )


def _collapse_log_value(value: str | None) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(value.split())
    return collapsed or None


def _truncate_log_value(value: str) -> str:
    if len(value) <= _MAX_LOG_VALUE_CHARS:
        return value
    return f"{value[:_MAX_LOG_VALUE_CHARS]}... [truncated]"
