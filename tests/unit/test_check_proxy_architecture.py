from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_checker_module() -> ModuleType:
    script_path = REPO_ROOT / "scripts" / "check_proxy_architecture.py"
    spec = importlib.util.spec_from_file_location("check_proxy_architecture", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_proxy_fixture(root: Path) -> Path:
    proxy_dir = root / "app" / "modules" / "proxy"
    service_dir = proxy_dir / "_service"
    (service_dir / "http_bridge").mkdir(parents=True)
    (service_dir / "streaming").mkdir()
    (service_dir / "websocket").mkdir()

    (proxy_dir / "service.py").write_text(
        "class ProxyService:\n    def handle(self) -> None:\n        pass\n",
        encoding="utf-8",
    )
    (proxy_dir / "load_balancer.py").write_text(
        "class LoadBalancer:\n    async def select_account(self) -> None:\n        return None\n",
        encoding="utf-8",
    )
    (service_dir / "__init__.py").write_text("", encoding="utf-8")
    (service_dir / "support.py").write_text("VALUE = 1\n", encoding="utf-8")
    (service_dir / "http_bridge" / "mixin.py").write_text("# HTTP bridge\n", encoding="utf-8")
    (service_dir / "streaming" / "mixin.py").write_text("# Streaming\n", encoding="utf-8")
    (service_dir / "websocket" / "__init__.py").write_text("", encoding="utf-8")
    shim = "from app.modules.proxy._service.support import VALUE\n"
    (proxy_dir / "_support.py").write_text(shim, encoding="utf-8")
    (proxy_dir / "_warmup.py").write_text(shim, encoding="utf-8")
    return proxy_dir


def _configure_fixture(checker: ModuleType, root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    proxy_dir = _write_proxy_fixture(root)
    service_dir = proxy_dir / "_service"
    monkeypatch.setattr(checker, "ROOT", root)
    monkeypatch.setattr(checker, "PROXY_DIR", proxy_dir)
    monkeypatch.setattr(checker, "SERVICE_PATH", proxy_dir / "service.py")
    monkeypatch.setattr(checker, "LOAD_BALANCER_PATH", proxy_dir / "load_balancer.py")
    monkeypatch.setattr(checker, "_SERVICE_DIR", service_dir)
    monkeypatch.setattr(checker, "SERVICE_PACKAGE_DIR", service_dir)
    monkeypatch.setattr(checker, "HTTP_BRIDGE_MIXIN_PATH", service_dir / "http_bridge" / "mixin.py")
    monkeypatch.setattr(checker, "STREAMING_MIXIN_PATH", service_dir / "streaming" / "mixin.py")
    monkeypatch.setattr(checker, "MAX_SERVICE_LINES", 20)
    monkeypatch.setattr(checker, "MAX_LOAD_BALANCER_LINES", 20)
    monkeypatch.setattr(checker, "MAX_HTTP_BRIDGE_MIXIN_LINES", 20)
    monkeypatch.setattr(checker, "MAX_STREAMING_MIXIN_LINES", 20)
    monkeypatch.setattr(checker, "MAX_PROXY_SERVICE_METHOD_LINES", 10)
    monkeypatch.setattr(checker, "MAX_LOAD_BALANCER_SELECT_ACCOUNT_LINES", 10)
    monkeypatch.setattr(checker, "REQUIRED_SERVICE_PACKAGES", {"http_bridge", "streaming", "websocket"})
    monkeypatch.setattr(checker, "REQUIRED_SERVICE_MODULES", {"__init__.py", "support.py"})
    monkeypatch.setattr(checker, "REQUIRED_SERVICE_FACADE_NAMES", {"ProxyService"})
    return proxy_dir


def test_main_reports_simultaneous_violations_in_stable_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker_module()
    _configure_fixture(checker, tmp_path, monkeypatch)
    monkeypatch.setattr(checker, "MAX_SERVICE_LINES", 1)
    monkeypatch.setattr(checker, "MAX_LOAD_BALANCER_LINES", 1)

    assert checker.main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "proxy architecture check failed: service.py has 3 lines; limit is 1",
        "proxy architecture check failed: load_balancer.py has 3 lines; limit is 1",
    ]


def test_main_skips_only_dependent_ast_checks_after_parse_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker_module()
    proxy_dir = _configure_fixture(checker, tmp_path, monkeypatch)
    (proxy_dir / "service.py").write_text("class ProxyService(:\n", encoding="utf-8")
    monkeypatch.setattr(checker, "MAX_LOAD_BALANCER_SELECT_ACCOUNT_LINES", 1)
    monkeypatch.setattr(
        checker,
        "REQUIRED_SERVICE_PACKAGES",
        {*checker.REQUIRED_SERVICE_PACKAGES, "missing_domain"},
    )

    assert checker.main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    failures = captured.err.splitlines()
    assert len(failures) == 3
    assert failures[0].startswith("proxy architecture check failed: app/modules/proxy/service.py could not be parsed:")
    assert failures[1] == ("proxy architecture check failed: LoadBalancer.select_account spans 2 lines; limit is 1")
    assert failures[2] == ("proxy architecture check failed: missing required proxy _service packages: missing_domain")


def test_main_clean_fixture_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker_module()
    _configure_fixture(checker, tmp_path, monkeypatch)

    assert checker.main() == 0

    captured = capsys.readouterr()
    assert captured.out == "proxy architecture checks passed\n"
    assert captured.err == ""


def test_repository_proxy_architecture_passes(capsys: pytest.CaptureFixture[str]) -> None:
    checker = _load_checker_module()

    assert checker.main() == 0

    captured = capsys.readouterr()
    assert captured.out == "proxy architecture checks passed\n"
    assert captured.err == ""
