from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable
from typing import cast

from fastapi import Request
from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter, DefaultFormatter

from app.core.utils.request_id import get_request_id


def _utc_converter(seconds: float | None) -> time.struct_time:
    return time.gmtime(seconds)


class UtcDefaultFormatter(DefaultFormatter):
    converter: Callable[[float | None], time.struct_time] = staticmethod(_utc_converter)


class UtcAccessFormatter(AccessFormatter):
    converter: Callable[[float | None], time.struct_time] = staticmethod(_utc_converter)


type LogConfigValue = str | bool | None | dict[str, "LogConfigValue"]
type LogConfig = dict[str, LogConfigValue]


def build_log_config() -> LogConfig:
    config = copy.deepcopy(LOGGING_CONFIG)
    formatters = config.setdefault("formatters", {})
    formatters["default"] = {
        "()": "app.core.runtime_logging.UtcDefaultFormatter",
        "fmt": "%(asctime)s %(levelprefix)s %(name)s %(message)s",
        "datefmt": "%Y-%m-%dT%H:%M:%SZ",
        "use_colors": None,
    }
    formatters["access"] = {
        "()": "app.core.runtime_logging.UtcAccessFormatter",
        "fmt": '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        "datefmt": "%Y-%m-%dT%H:%M:%SZ",
        "use_colors": None,
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
        code,
        _collapse_log_value(message),
        exc_info=exc_info,
    )


def _collapse_log_value(value: str | None) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(value.split())
    return collapsed or None
