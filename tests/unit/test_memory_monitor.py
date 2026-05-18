from __future__ import annotations

import builtins
import importlib
import sys

import pytest

pytestmark = pytest.mark.unit


def test_get_rss_bytes_returns_positive_number():
    from app.core.resilience import memory_monitor

    rss = memory_monitor.get_rss_bytes()
    assert isinstance(rss, int)
    assert rss > 0


def test_is_memory_pressure_returns_false_when_threshold_disabled():
    from app.core.resilience import memory_monitor

    memory_monitor.configure(warning_threshold_mb=0, reject_threshold_mb=0)
    assert memory_monitor.is_memory_pressure() is False


def test_memory_monitor_imports_on_windows_without_resource(monkeypatch: pytest.MonkeyPatch):
    module_name = "app.core.resilience.memory_monitor"
    original_module = sys.modules.get(module_name)
    sys.modules.pop(module_name, None)
    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None):
        if name == "resource":
            raise ModuleNotFoundError("No module named 'resource'")
        return real_import_module(name, package)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    try:
        module = importlib.import_module(module_name)
        assert module._resource is None
        assert callable(module.get_rss_bytes)
    finally:
        sys.modules.pop(module_name, None)
        if original_module is not None:
            sys.modules[module_name] = original_module


def test_get_rss_bytes_returns_zero_when_no_provider_available(monkeypatch: pytest.MonkeyPatch):
    from app.core.resilience import memory_monitor

    real_import = builtins.__import__

    def fake_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
        if name == "psutil":
            raise ImportError("psutil unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(memory_monitor, "_resource", None)
    monkeypatch.setattr(memory_monitor, "_get_windows_rss_bytes", lambda: None)
    monkeypatch.setattr(memory_monitor, "_rss_provider_warning_logged", False)
    monkeypatch.setattr(memory_monitor.sys, "platform", "win32")

    assert memory_monitor.get_rss_bytes() == 0
