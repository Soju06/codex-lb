from __future__ import annotations

import logging
import time
from typing import Literal

from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    image_request_duration_seconds,
    image_requests_total,
)
from app.core.openai.images import is_supported_image_model

logger = logging.getLogger("app.modules.proxy.api")

ImageRoute = Literal["generations", "edits"]


def _bounded_model_label(model: str | None) -> str:
    if model is None or not model:
        return "unknown"
    if is_supported_image_model(model):
        return model
    return "invalid"


def record_images_route_observability(
    *,
    route: ImageRoute,
    model: str | None,
    stream: bool,
    status: int,
    outcome: str,
    started_at: float,
) -> None:
    duration_seconds = max(time.perf_counter() - started_at, 0.0)
    model_label = _bounded_model_label(model)
    status_label = str(status)
    stream_label = "true" if stream else "false"
    if PROMETHEUS_AVAILABLE and image_requests_total is not None and image_request_duration_seconds is not None:
        labels = {
            "route": route,
            "model": model_label,
            "stream": stream_label,
            "status": status_label,
            "outcome": outcome,
        }
        image_requests_total.labels(**labels).inc()
        image_request_duration_seconds.labels(**labels).observe(duration_seconds)
    logger.log(
        logging.INFO if status < 400 else logging.WARNING,
        "images_route_complete route=%s model=%s stream=%s status=%s outcome=%s duration_ms=%.2f",
        route,
        model_label,
        stream_label,
        status,
        outcome,
        duration_seconds * 1000.0,
    )
