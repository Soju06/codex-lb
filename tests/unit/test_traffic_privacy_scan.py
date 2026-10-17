from __future__ import annotations

from pathlib import Path

import pytest

from scripts.traffic_analysis.privacy_scan import scan_tree


def test_privacy_scan_accepts_redacted_metadata(tmp_path: Path) -> None:
    (tmp_path / "capture.jsonl").write_text(
        '{"authorization":"[REDACTED]","access_token":"[SHA256:abc:12]"}\n',
        encoding="utf-8",
    )

    result = scan_tree(tmp_path)

    assert result["passed"] is True
    assert result["findings"] == []


def test_privacy_scan_reports_kinds_without_echoing_secret(tmp_path: Path) -> None:
    secret = "sk-examplecredential123456789"
    (tmp_path / "capture.jsonl").write_text(f'{{"authorization":"Bearer {secret}"}}\n', encoding="utf-8")

    result = scan_tree(tmp_path)

    assert result["passed"] is False
    assert result["findings"] == [{"path": "capture.jsonl", "kinds": ["bearer_token", "secret_key"]}]
    assert secret not in str(result)


def test_privacy_scan_fails_closed_on_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("not scanned", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside)

    result = scan_tree(tmp_path)

    assert result["passed"] is False
    assert result["findings"] == [{"path": "link", "kinds": ["symlink"]}]


def test_privacy_scan_rejects_missing_or_non_directory_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="existing directory"):
        scan_tree(tmp_path / "missing")

    file_root = tmp_path / "file"
    file_root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="existing directory"):
        scan_tree(file_root)


def test_privacy_scan_detects_long_oauth_field_across_chunk_boundary(tmp_path: Path) -> None:
    prefix = b"X" * (1024 * 1024 - len(b'{"access_token":"') - 600)
    token = b"a" * 600
    (tmp_path / "capture.jsonl").write_bytes(prefix + b'{"access_token":"' + token + b'"}')

    result = scan_tree(tmp_path)

    assert result["passed"] is False
    assert result["findings"] == [{"path": "capture.jsonl", "kinds": ["oauth_token_field"]}]
