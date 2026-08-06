from __future__ import annotations

from importlib import import_module

shutdown_state = import_module("app.core.shutdown")


def __getattr__(name: str) -> bool:
    if name == "_draining":
        return shutdown_state.is_draining()
    raise AttributeError(name)
