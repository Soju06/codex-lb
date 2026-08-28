from __future__ import annotations

import errno
import logging

import pytest

import app.main as main_module

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (SystemExit(1), True),
        (SystemExit(0), False),
        (OSError(errno.EADDRINUSE, "address already in use"), True),
        (OSError(errno.EADDRNOTAVAIL, "cannot assign requested address"), True),
        (OSError(errno.EACCES, "permission denied"), False),
        (RuntimeError("boom"), False),
    ],
)
def test_is_metrics_bind_conflict(exc: BaseException, expected: bool) -> None:
    assert main_module._is_metrics_bind_conflict(exc) is expected


def test_bind_conflict_is_benign_only_in_multiproc_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    conflict = OSError(errno.EADDRINUSE, "address already in use")

    monkeypatch.setattr(main_module, "MULTIPROCESS_MODE", True)
    assert main_module._is_benign_metrics_bind_failure(conflict) is True

    monkeypatch.setattr(main_module, "MULTIPROCESS_MODE", False)
    assert main_module._is_benign_metrics_bind_failure(conflict) is False


def test_non_multiproc_bind_conflict_logs_error_with_remediation(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="app.main"):
        main_module._log_non_multiproc_metrics_bind_conflict(9090)

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "9090" in message
    assert "PROMETHEUS_MULTIPROC_DIR" in message
    assert "only the winning worker" in message
