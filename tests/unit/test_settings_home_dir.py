from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.core.config.settings import (
    DEFAULT_CONVERSATION_ARCHIVE_DIR,
    DEFAULT_DATABASE_URL,
    DEFAULT_ENCRYPTION_KEY_FILE,
    DOCKER_DATA_DIR,
    Settings,
    _default_home_dir,
)


def _settings_from_env_file(env_file: Path) -> Settings:
    kwargs: dict[str, Any] = {"_env_file": env_file}
    return Settings(**kwargs)


@pytest.mark.parametrize(
    ("env_dir", "home_exists", "in_container", "expected"),
    [
        ("/tmp/custom", False, False, Path("/tmp/custom")),
        (None, True, False, Path("/home/user") / ".codex-lb"),
        (None, True, True, Path("/home/user") / ".codex-lb"),
        (None, False, True, DOCKER_DATA_DIR),
        (None, False, False, Path("/home/user") / ".codex-lb"),
    ],
)
def test_default_home_dir_precedence(
    env_dir: str | None,
    home_exists: bool,
    in_container: bool,
    expected: Path,
) -> None:
    with (
        patch("app.core.config.settings.os.getenv", return_value=env_dir),
        patch("app.core.config.settings.Path.home", return_value=Path("/home/user")),
        patch("app.core.config.settings._in_container", return_value=in_container),
        patch.object(Path, "exists", return_value=home_exists),
    ):
        assert _default_home_dir() == expected


def test_data_dir_from_env_file_updates_related_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEX_LB_DATA_DIR", raising=False)
    monkeypatch.delenv("CODEX_LB_DATABASE_URL", raising=False)
    monkeypatch.delenv("CODEX_LB_ENCRYPTION_KEY_FILE", raising=False)
    monkeypatch.delenv("CODEX_LB_CONVERSATION_ARCHIVE_DIR", raising=False)
    data_dir = tmp_path / "configured"
    env_file = tmp_path / ".env"
    env_file.write_text(f"CODEX_LB_DATA_DIR={data_dir}\n", encoding="utf-8")

    settings = _settings_from_env_file(env_file)

    assert settings.data_dir == data_dir
    assert settings.database_url == f"sqlite+aiosqlite:///{data_dir / 'store.db'}"
    assert settings.encryption_key_file == data_dir / "encryption.key"
    assert settings.conversation_archive_dir == data_dir / "conversation-archive"


def test_blank_data_dir_from_env_file_uses_default_home_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODEX_LB_DATA_DIR", raising=False)
    monkeypatch.delenv("CODEX_LB_DATABASE_URL", raising=False)
    monkeypatch.delenv("CODEX_LB_ENCRYPTION_KEY_FILE", raising=False)
    monkeypatch.delenv("CODEX_LB_CONVERSATION_ARCHIVE_DIR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("CODEX_LB_DATA_DIR=   \n", encoding="utf-8")

    expected_data_dir = Path("/home/user") / ".codex-lb"
    with (
        patch("app.core.config.settings.Path.home", return_value=Path("/home/user")),
        patch("app.core.config.settings._in_container", return_value=False),
        patch.object(Path, "exists", return_value=False),
    ):
        settings = _settings_from_env_file(env_file)

    assert settings.data_dir == expected_data_dir
    assert settings.database_url == f"sqlite+aiosqlite:///{expected_data_dir / 'store.db'}"
    assert settings.encryption_key_file == expected_data_dir / "encryption.key"
    assert settings.conversation_archive_dir == expected_data_dir / "conversation-archive"


def test_blank_data_dir_from_process_env_uses_default_home_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_DATA_DIR", "   ")

    expected_data_dir = Path("/home/user") / ".codex-lb"
    with (
        patch("app.core.config.settings.Path.home", return_value=Path("/home/user")),
        patch("app.core.config.settings._in_container", return_value=False),
        patch.object(Path, "exists", return_value=False),
    ):
        assert _default_home_dir() == expected_data_dir


def test_data_dir_keeps_explicit_related_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEX_LB_DATA_DIR", raising=False)
    monkeypatch.delenv("CODEX_LB_DATABASE_URL", raising=False)
    monkeypatch.delenv("CODEX_LB_ENCRYPTION_KEY_FILE", raising=False)
    monkeypatch.delenv("CODEX_LB_CONVERSATION_ARCHIVE_DIR", raising=False)
    data_dir = tmp_path / "configured"
    archive_dir = tmp_path / "archive"
    key_file = tmp_path / "key"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"CODEX_LB_DATA_DIR={data_dir}",
                "CODEX_LB_DATABASE_URL=sqlite+aiosqlite:///explicit.db",
                f"CODEX_LB_ENCRYPTION_KEY_FILE={key_file}",
                f"CODEX_LB_CONVERSATION_ARCHIVE_DIR={archive_dir}",
            ]
        ),
        encoding="utf-8",
    )

    settings = _settings_from_env_file(env_file)

    assert settings.data_dir == data_dir
    assert settings.database_url == "sqlite+aiosqlite:///explicit.db"
    assert settings.encryption_key_file == key_file
    assert settings.conversation_archive_dir == archive_dir


def test_data_dir_keeps_explicit_related_overrides_that_equal_old_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODEX_LB_DATA_DIR", raising=False)
    monkeypatch.delenv("CODEX_LB_DATABASE_URL", raising=False)
    monkeypatch.delenv("CODEX_LB_ENCRYPTION_KEY_FILE", raising=False)
    monkeypatch.delenv("CODEX_LB_CONVERSATION_ARCHIVE_DIR", raising=False)
    data_dir = tmp_path / "configured"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"CODEX_LB_DATA_DIR={data_dir}",
                f"CODEX_LB_DATABASE_URL={DEFAULT_DATABASE_URL}",
                f"CODEX_LB_ENCRYPTION_KEY_FILE={DEFAULT_ENCRYPTION_KEY_FILE}",
                f"CODEX_LB_CONVERSATION_ARCHIVE_DIR={DEFAULT_CONVERSATION_ARCHIVE_DIR}",
            ]
        ),
        encoding="utf-8",
    )

    settings = _settings_from_env_file(env_file)

    assert settings.data_dir == data_dir
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.encryption_key_file == DEFAULT_ENCRYPTION_KEY_FILE
    assert settings.conversation_archive_dir == DEFAULT_CONVERSATION_ARCHIVE_DIR
