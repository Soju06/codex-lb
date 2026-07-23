import importlib.util
from pathlib import Path
from typing import cast

import pytest

from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.config import settings as settings_module
from app.core.config.settings import Settings


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
    smoke_harness._run_backend(123)

    settings = Settings()
    assert settings.data_dir == tmp_path
    assert settings.database_url == f"sqlite+aiosqlite:///{tmp_path / 'store.db'}"
    assert settings.dashboard_auth_mode == DashboardAuthMode.STANDARD
    assert "CODEX_LB_DATABASE_URL" not in settings_module._effective_environ()
    assert "CODEX_LB_DASHBOARD_AUTH_MODE" not in settings_module._effective_environ()
    assert captured == {"app": "app.main:app", "fd": 123, "log_level": "warning"}
