from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_architecture_spec(
    path: Path,
    *,
    service_lines: int = 20,
    load_balancer_lines: int = 20,
    load_balancer_select_account_lines: int = 10,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            (
                "# proxy-architecture Specification",
                "",
                "<!-- proxy-architecture-thresholds:start -->",
                "```toml",
                f"service_lines = {service_lines}",
                f"load_balancer_lines = {load_balancer_lines}",
                "http_bridge_mixin_lines = 20",
                "streaming_mixin_lines = 20",
                "proxy_service_method_lines = 10",
                f"load_balancer_select_account_lines = {load_balancer_select_account_lines}",
                "```",
                "<!-- proxy-architecture-thresholds:end -->",
                "",
            )
        ),
        encoding="utf-8",
    )


def _load_checker_module() -> ModuleType:
    script_path = REPO_ROOT / "scripts" / "check_proxy_architecture.py"
    spec = importlib.util.spec_from_file_location("check_proxy_architecture", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.modules.get(spec.name) is module:
            del sys.modules[spec.name]
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
    spec_path = root / "openspec" / "specs" / "proxy-architecture" / "spec.md"
    _write_architecture_spec(spec_path)
    monkeypatch.setattr(checker, "ROOT", root)
    monkeypatch.setattr(checker, "PROXY_DIR", proxy_dir)
    monkeypatch.setattr(checker, "SERVICE_PATH", proxy_dir / "service.py")
    monkeypatch.setattr(checker, "LOAD_BALANCER_PATH", proxy_dir / "load_balancer.py")
    monkeypatch.setattr(checker, "_SERVICE_DIR", service_dir)
    monkeypatch.setattr(checker, "SERVICE_PACKAGE_DIR", service_dir)
    monkeypatch.setattr(checker, "HTTP_BRIDGE_MIXIN_PATH", service_dir / "http_bridge" / "mixin.py")
    monkeypatch.setattr(checker, "STREAMING_MIXIN_PATH", service_dir / "streaming" / "mixin.py")
    monkeypatch.setattr(checker, "PROXY_ARCHITECTURE_SPEC_PATH", spec_path)
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
    _write_architecture_spec(checker.PROXY_ARCHITECTURE_SPEC_PATH, service_lines=1, load_balancer_lines=1)

    assert checker.main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "proxy architecture check failed: service.py has 3 lines; limit is 1",
        "proxy architecture check failed: load_balancer.py has 3 lines; limit is 1",
    ]


def test_main_uses_openspec_owned_thresholds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker_module()
    _configure_fixture(checker, tmp_path, monkeypatch)
    spec_path = checker.PROXY_ARCHITECTURE_SPEC_PATH
    _write_architecture_spec(spec_path, service_lines=2)

    assert checker.main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "proxy architecture check failed: service.py has 3 lines; limit is 2",
    ]


def test_main_skips_only_dependent_ast_checks_after_parse_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker_module()
    proxy_dir = _configure_fixture(checker, tmp_path, monkeypatch)
    (proxy_dir / "service.py").write_text("class ProxyService(:\n", encoding="utf-8")
    _write_architecture_spec(checker.PROXY_ARCHITECTURE_SPEC_PATH, load_balancer_select_account_lines=1)
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


@pytest.mark.parametrize(
    ("transform", "expected_detail"),
    [
        pytest.param(
            lambda text: text.replace("streaming_mixin_lines = 20\n", ""),
            "architecture threshold keys are invalid: missing streaming_mixin_lines",
            id="missing-key",
        ),
        pytest.param(
            lambda text: text.replace("streaming_mixin_lines = 20", "streaming_mixin_lines = 20\nextra_lines = 1"),
            "architecture threshold keys are invalid: unknown extra_lines",
            id="unknown-key",
        ),
        pytest.param(
            lambda text: text.replace("service_lines = 20", "service_lines = 0"),
            "architecture threshold service_lines must be a positive integer",
            id="non-positive",
        ),
        pytest.param(
            lambda text: text.replace("service_lines = 20", 'service_lines = "20"'),
            "architecture threshold service_lines must be a positive integer",
            id="non-integer",
        ),
    ],
)
def test_main_rejects_invalid_openspec_thresholds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    transform: Callable[[str], str],
    expected_detail: str,
) -> None:
    checker = _load_checker_module()
    _configure_fixture(checker, tmp_path, monkeypatch)
    spec_path = checker.PROXY_ARCHITECTURE_SPEC_PATH
    spec_path.write_text(transform(spec_path.read_text(encoding="utf-8")), encoding="utf-8")

    assert checker.main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        f"proxy architecture check failed: openspec/specs/proxy-architecture/spec.md {expected_detail}",
    ]


@pytest.mark.parametrize(
    ("transform", "expected_detail"),
    [
        pytest.param(
            lambda text: text.replace("service_lines = 20", "service_lines ="),
            "architecture threshold block contains invalid TOML",
            id="malformed-toml",
        ),
        pytest.param(
            lambda text: text + "\n<!-- proxy-architecture-thresholds:start -->\n",
            "must contain exactly one marked architecture threshold block",
            id="duplicate-marker",
        ),
        pytest.param(
            lambda text: text.replace("```toml", "```text"),
            "architecture threshold block must contain one TOML fence",
            id="wrong-fence",
        ),
    ],
)
def test_main_rejects_malformed_openspec_threshold_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    transform: Callable[[str], str],
    expected_detail: str,
) -> None:
    checker = _load_checker_module()
    _configure_fixture(checker, tmp_path, monkeypatch)
    spec_path = checker.PROXY_ARCHITECTURE_SPEC_PATH
    spec_path.write_text(transform(spec_path.read_text(encoding="utf-8")), encoding="utf-8")

    assert checker.main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        f"proxy architecture check failed: openspec/specs/proxy-architecture/spec.md {expected_detail}",
    ]


def test_main_continues_unrelated_checks_after_threshold_definition_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker_module()
    _configure_fixture(checker, tmp_path, monkeypatch)
    spec_path = checker.PROXY_ARCHITECTURE_SPEC_PATH
    spec_path.write_text(
        spec_path.read_text(encoding="utf-8").replace("service_lines = 20", "service_lines ="),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        checker,
        "REQUIRED_SERVICE_PACKAGES",
        {*checker.REQUIRED_SERVICE_PACKAGES, "missing_domain"},
    )

    assert checker.main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "proxy architecture check failed: openspec/specs/proxy-architecture/spec.md "
        "architecture threshold block contains invalid TOML",
        "proxy architecture check failed: missing required proxy _service packages: missing_domain",
    ]


def test_main_rejects_non_utf8_threshold_definition_and_continues_unrelated_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker_module()
    _configure_fixture(checker, tmp_path, monkeypatch)
    checker.PROXY_ARCHITECTURE_SPEC_PATH.write_bytes(b"\xff")
    monkeypatch.setattr(
        checker,
        "REQUIRED_SERVICE_PACKAGES",
        {*checker.REQUIRED_SERVICE_PACKAGES, "missing_domain"},
    )

    assert checker.main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "proxy architecture check failed: openspec/specs/proxy-architecture/spec.md "
        "threshold definition is not valid UTF-8",
        "proxy architecture check failed: missing required proxy _service packages: missing_domain",
    ]


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
