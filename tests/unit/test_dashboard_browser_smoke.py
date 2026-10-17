import importlib.util
from pathlib import Path
from typing import cast

import pytest

import app.main as main_module
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.config import settings as settings_module
from app.core.config.settings import Settings
from tests.conftest import BACKGROUND_LOOP_BUILDERS


def _load_smoke_harness():
    script_path = Path(__file__).parents[2] / "scripts" / "run_dashboard_browser_smoke.py"
    specification = importlib.util.spec_from_file_location("dashboard_browser_smoke", script_path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_smoke_backend_disables_dotenv_sources_before_importing_the_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dotenv = tmp_path / ".env.local"
    dotenv.write_text(
        "CODEX_LB_DATABASE_URL=sqlite+aiosqlite:////must-not-load.db\nCODEX_LB_DASHBOARD_AUTH_MODE=trusted_header\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_LB_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CODEX_LB_DATABASE_URL", raising=False)
    monkeypatch.delenv("CODEX_LB_DASHBOARD_AUTH_MODE", raising=False)
    monkeypatch.setattr(settings_module, "ENV_FILES", (dotenv,))
    monkeypatch.setitem(cast(dict[str, object], Settings.model_config), "env_file", (dotenv,))

    smoke_harness = _load_smoke_harness()
    captured: dict[str, object] = {}

    def fake_run(app: str, **kwargs: object) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)
    disabled_modules: list[object] = []
    monkeypatch.setattr(smoke_harness, "_disable_background_loops", disabled_modules.append)
    smoke_harness._run_backend(123)

    assert disabled_modules == [main_module]
    settings = Settings()
    assert settings.data_dir == tmp_path
    assert settings.database_url == f"sqlite+aiosqlite:///{tmp_path / 'store.db'}"
    assert settings.dashboard_auth_mode == DashboardAuthMode.STANDARD
    assert "CODEX_LB_DATABASE_URL" not in settings_module._effective_environ()
    assert "CODEX_LB_DASHBOARD_AUTH_MODE" not in settings_module._effective_environ()
    assert captured == {"app": "app.main:app", "fd": 123, "log_level": "warning"}


def test_smoke_main_requires_frontend_build_before_allocating_resources(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    smoke_harness = _load_smoke_harness()

    def fail_if_run() -> int:
        pytest.fail("smoke resources were allocated before the frontend-build preflight")

    monkeypatch.setattr(smoke_harness, "run", fail_if_run)

    assert smoke_harness.main([]) == 2
    assert (
        capsys.readouterr().err
        == "Run `make test-dashboard-browser-smoke` so app/static is rebuilt before the browser smoke test.\n"
    )


def test_smoke_main_runs_after_frontend_build_acknowledgement(monkeypatch: pytest.MonkeyPatch) -> None:
    smoke_harness = _load_smoke_harness()
    monkeypatch.setattr(smoke_harness, "run", lambda: 17)

    assert smoke_harness.main(["--frontend-built"]) == 17


def test_smoke_main_dispatches_backend_without_frontend_build_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke_harness = _load_smoke_harness()
    captured: list[int] = []
    monkeypatch.setattr(smoke_harness, "_run_backend", captured.append)

    assert smoke_harness.main(["--backend-fd", "123"]) == 0
    assert captured == [123]


@pytest.mark.parametrize("listener_fd", ["not-an-integer", "-1"])
def test_smoke_main_rejects_invalid_backend_fd(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    listener_fd: str,
) -> None:
    smoke_harness = _load_smoke_harness()

    def fail_if_run(_listener_fd: int) -> None:
        pytest.fail("backend started with an invalid listener descriptor")

    monkeypatch.setattr(smoke_harness, "_run_backend", fail_if_run)

    assert smoke_harness.main(["--backend-fd", listener_fd]) == 2
    assert capsys.readouterr().err == "Invalid --backend-fd value.\n"


def test_smoke_backend_disables_the_same_background_loops_as_the_test_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke_harness = _load_smoke_harness()
    assert smoke_harness.BACKGROUND_LOOP_BUILDERS == BACKGROUND_LOOP_BUILDERS

    smoke_harness._disable_background_loops(main_module, setter=monkeypatch.setattr)

    for builder_name in BACKGROUND_LOOP_BUILDERS:
        assert isinstance(getattr(main_module, builder_name)(), smoke_harness._NoopScheduler), builder_name
    assert main_module.start_live_usage_ingestor() is None


def test_smoke_environment_does_not_export_background_loop_env_kill_switches(tmp_path: Path) -> None:
    smoke_harness = _load_smoke_harness()
    environment = smoke_harness._smoke_environment(tmp_path)

    exported = sorted(
        key
        for key in environment
        if key.startswith("CODEX_LB_")
        and key.endswith("_ENABLED")
        and key
        not in {
            "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED",
            "CODEX_LB_METRICS_ENABLED",
            "CODEX_LB_OTEL_ENABLED",
        }
    )
    assert exported == []
