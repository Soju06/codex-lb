from __future__ import annotations

import importlib
import sys

import pytest

import app.core.config.settings as settings_module

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _restore_settings_module():
    """Reload-based tests rebind module globals (e.g. ``get_settings``) to fresh
    objects. Other modules import those globals by value at import time, so a
    leaked reload leaves them pointing at a stale ``get_settings`` whose
    ``lru_cache`` the conftest fixture can no longer clear. Snapshot and restore
    the module namespace so dependents stay consistent across tests.
    """
    saved = dict(settings_module.__dict__)
    try:
        yield
    finally:
        settings_module.__dict__.clear()
        settings_module.__dict__.update(saved)
        settings_module.get_settings.cache_clear()


def test_packaged_runtime_reads_env_file_from_executable_directory(tmp_path) -> None:
    executable_dir = tmp_path / "release"
    executable_dir.mkdir()
    (executable_dir / ".env.local").write_text("CODEX_LB_LOG_FORMAT=json\n", encoding="utf-8")
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(executable_dir / "codex-lb"), raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)
        monkeypatch.delenv("CODEX_LB_LOG_FORMAT", raising=False)

        reloaded = importlib.reload(settings_module)
        settings = reloaded.Settings()

        assert reloaded.BASE_DIR == executable_dir
        assert settings.log_format == "json"

    importlib.reload(settings_module)


def test_packaged_runtime_uses_macos_application_support_for_fresh_installs(tmp_path) -> None:
    executable_dir = tmp_path / "release"
    executable_dir.mkdir()

    home_dir = tmp_path / "home"
    macos_home_dir = home_dir / "Library" / "Application Support" / "codex-lb"

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "platform", "darwin", raising=False)
        monkeypatch.setattr(sys, "executable", str(executable_dir / "codex-lb"), raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)

        reloaded = importlib.reload(settings_module)

        assert reloaded.DEFAULT_HOME_DIR == macos_home_dir
        assert reloaded.DEFAULT_DB_PATH == macos_home_dir / "store.db"
        assert reloaded.DEFAULT_ENCRYPTION_KEY_FILE == macos_home_dir / "encryption.key"

    importlib.reload(settings_module)


def test_packaged_runtime_keeps_legacy_home_dir_for_existing_users(tmp_path) -> None:
    executable_dir = tmp_path / "release"
    executable_dir.mkdir()

    home_dir = tmp_path / "home"
    legacy_home_dir = home_dir / ".codex-lb"
    legacy_home_dir.mkdir(parents=True)
    (legacy_home_dir / ".env.local").write_text("CODEX_LB_LOG_FORMAT=json\n", encoding="utf-8")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "platform", "darwin", raising=False)
        monkeypatch.setattr(sys, "executable", str(executable_dir / "codex-lb"), raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)
        monkeypatch.delenv("CODEX_LB_LOG_FORMAT", raising=False)

        reloaded = importlib.reload(settings_module)
        settings = reloaded.Settings()

        assert reloaded.DEFAULT_HOME_DIR == legacy_home_dir
        assert settings.log_format == "json"

    importlib.reload(settings_module)


def test_packaged_runtime_prefers_macos_user_overrides_over_legacy_home_dir(tmp_path) -> None:
    executable_dir = tmp_path / "release"
    executable_dir.mkdir()
    (executable_dir / ".env.local").write_text("CODEX_LB_LOG_FORMAT=text\n", encoding="utf-8")

    home_dir = tmp_path / "home"
    legacy_home_dir = home_dir / ".codex-lb"
    legacy_home_dir.mkdir(parents=True)
    (legacy_home_dir / ".env.local").write_text("CODEX_LB_LOG_FORMAT=text\n", encoding="utf-8")

    macos_home_dir = home_dir / "Library" / "Application Support" / "codex-lb"
    macos_home_dir.mkdir(parents=True)
    (macos_home_dir / ".env.local").write_text("CODEX_LB_LOG_FORMAT=json\n", encoding="utf-8")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "platform", "darwin", raising=False)
        monkeypatch.setattr(sys, "executable", str(executable_dir / "codex-lb"), raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)
        monkeypatch.delenv("CODEX_LB_LOG_FORMAT", raising=False)

        reloaded = importlib.reload(settings_module)
        settings = reloaded.Settings()

        assert reloaded.DEFAULT_HOME_DIR == macos_home_dir
        assert settings.log_format == "json"

    importlib.reload(settings_module)


def test_source_runtime_keeps_repo_base_dir() -> None:
    reloaded = importlib.reload(settings_module)

    assert reloaded.BASE_DIR == reloaded.Path(__file__).resolve().parents[2]
