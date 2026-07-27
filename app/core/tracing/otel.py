from __future__ import annotations

import logging
import re
from importlib import import_module
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI
from starlette.types import Scope
from yarl import URL

logger = logging.getLogger(__name__)

_otel_initialized = False
_LIVE_CALL_UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")


class _AttributeSpan(Protocol):
    def set_attribute(self, key: str, value: str) -> None: ...


def _is_live_call_id(call_id: str) -> bool:
    return "/" not in call_id and (call_id.startswith("rtc_") or _LIVE_CALL_UUID_PATTERN.fullmatch(call_id) is not None)


def _redacted_backend_live_path(path: str) -> str | None:
    for prefix in ("/backend-api/codex/v1/", "/backend-api/codex/"):
        call_id = path.removeprefix(prefix)
        if call_id != path and _is_live_call_id(call_id):
            return f"{prefix}<redacted>"
    return None


def _redacted_live_trace_path(path: str) -> str | None:
    if path.startswith("/v1/live/"):
        return "/v1/live/<redacted>"
    if redacted_path := _redacted_backend_live_path(path):
        return redacted_path
    if path == "/v1/realtime":
        return path
    return None


def _scope_trace_url(scope: Scope, path: str) -> str:
    scheme = scope.get("scheme")
    url_scheme = scheme if isinstance(scheme, str) and scheme else "http"
    host = ""
    for name, value in scope.get("headers", []):
        if name.lower() == b"host":
            host = value.decode("latin-1")
            break
    if not host:
        server = scope.get("server") or ("0.0.0.0", 80)
        host = str(server[0])
        if str(server[1]) != "80":
            host = f"{host}:{server[1]}"
    return f"{url_scheme}://{host}{path}"


def _redact_live_server_span(span: _AttributeSpan, scope: Scope) -> None:
    """Overwrite raw Live request attributes without mutating ASGI routing state."""

    raw_path = scope.get("path")
    if not isinstance(raw_path, str):
        return
    parsed_path = urlsplit(raw_path)
    redacted_path = _redacted_live_trace_path(parsed_path.path)
    if redacted_path is None:
        return

    redacted_url = _scope_trace_url(scope, redacted_path)
    span.set_attribute("http.target", redacted_path)
    span.set_attribute("http.url", redacted_url)
    span.set_attribute("url.full", redacted_url)
    span.set_attribute("url.path", redacted_path)
    span.set_attribute("url.query", "")
    span.set_attribute("url.fragment", "")


def _filter_aiohttp_trace_url(url: str | URL) -> str:
    """Redact private Live identifiers from exported aiohttp span URLs."""

    raw_url = str(url)
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return raw_url

    redacted_path = _redacted_live_trace_path(parsed.path)
    if redacted_path is None:
        return raw_url

    return urlunsplit((parsed.scheme, parsed.netloc, redacted_path, "", ""))


def _instrument_fastapi(app: FastAPI | None) -> None:
    try:
        instrumentation_module = import_module("opentelemetry.instrumentation.fastapi")
        FastAPIInstrumentor = getattr(instrumentation_module, "FastAPIInstrumentor")

        if app is not None:
            FastAPIInstrumentor.instrument_app(app, server_request_hook=_redact_live_server_span)
        else:
            FastAPIInstrumentor().instrument(server_request_hook=_redact_live_server_span)
    except ImportError:
        pass
    except Exception:
        logger.exception("Failed to auto-instrument FastAPI")


def init_tracing(service_name: str = "codex-lb", endpoint: str = "", app: FastAPI | None = None) -> bool:
    global _otel_initialized

    if _otel_initialized:
        # Process-global providers are already set up, but a factory-created
        # FastAPI instance (e.g. ``uvicorn --factory``) still needs its own
        # per-app instrumentation.
        if app is not None:
            _instrument_fastapi(app)
        return True

    try:
        trace = import_module("opentelemetry.trace")
        sdk_trace = import_module("opentelemetry.sdk.trace")
        sdk_resources = import_module("opentelemetry.sdk.resources")
        sdk_trace_export = import_module("opentelemetry.sdk.trace.export")
        Resource = getattr(sdk_resources, "Resource")
        SERVICE_NAME = getattr(sdk_resources, "SERVICE_NAME")
        TracerProvider = getattr(sdk_trace, "TracerProvider")
        BatchSpanProcessor = getattr(sdk_trace_export, "BatchSpanProcessor")

        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))

        if endpoint:
            try:
                exporter_module = import_module("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
                OTLPSpanExporter = getattr(exporter_module, "OTLPSpanExporter")

                exporter = OTLPSpanExporter(endpoint=endpoint)
                provider.add_span_processor(BatchSpanProcessor(exporter))
            except ImportError:
                logger.warning("OTLP exporter not available; tracing without export")

        trace.set_tracer_provider(provider)

        _instrument_fastapi(app)

        try:
            instrumentation_module = import_module("opentelemetry.instrumentation.aiohttp_client")
            AioHttpClientInstrumentor = getattr(instrumentation_module, "AioHttpClientInstrumentor")

            AioHttpClientInstrumentor().instrument(url_filter=_filter_aiohttp_trace_url)
        except ImportError:
            pass
        except Exception:
            logger.exception("Failed to auto-instrument aiohttp client")

        try:
            instrumentation_module = import_module("opentelemetry.instrumentation.sqlalchemy")
            SQLAlchemyInstrumentor = getattr(instrumentation_module, "SQLAlchemyInstrumentor")

            SQLAlchemyInstrumentor().instrument()
        except ImportError:
            pass
        except Exception:
            logger.exception("Failed to auto-instrument SQLAlchemy")

        _otel_initialized = True
        logger.info("OpenTelemetry tracing initialized (service=%s)", service_name)
        return True

    except ImportError:
        logger.warning(
            "opentelemetry packages not installed; tracing disabled. Install with: pip install codex-lb[tracing]"
        )
        return False


def is_initialized() -> bool:
    return _otel_initialized


def get_current_trace_id() -> str | None:
    try:
        trace = import_module("opentelemetry.trace")

        span = getattr(trace, "get_current_span")()
        ctx = span.get_span_context()
        if ctx and ctx.is_valid:
            return format(ctx.trace_id, "032x")
    except Exception:
        logger.debug("Failed to get current trace ID", exc_info=True)
    return None


def get_current_span_id() -> str | None:
    try:
        trace = import_module("opentelemetry.trace")

        span = getattr(trace, "get_current_span")()
        ctx = span.get_span_context()
        if ctx and ctx.is_valid:
            return format(ctx.span_id, "016x")
    except Exception:
        logger.debug("Failed to get current span ID", exc_info=True)
    return None
