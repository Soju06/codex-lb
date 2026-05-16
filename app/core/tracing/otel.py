from __future__ import annotations

import logging
from importlib import import_module

from fastapi import FastAPI

logger = logging.getLogger(__name__)

_otel_initialized = False


def init_tracing(service_name: str = "codex-lb", endpoint: str = "", app: FastAPI | None = None) -> bool:
    global _otel_initialized

    if _otel_initialized:
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

        try:
            instrumentation_module = import_module("opentelemetry.instrumentation.fastapi")
            FastAPIInstrumentor = getattr(instrumentation_module, "FastAPIInstrumentor")

            if app is not None:
                FastAPIInstrumentor.instrument_app(app)
            else:
                FastAPIInstrumentor().instrument()
        except ImportError:
            pass
        except Exception:
            logger.exception("Failed to auto-instrument FastAPI")

        try:
            instrumentation_module = import_module("opentelemetry.instrumentation.aiohttp_client")
            AioHttpClientInstrumentor = getattr(instrumentation_module, "AioHttpClientInstrumentor")

            AioHttpClientInstrumentor().instrument()
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
