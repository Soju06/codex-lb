from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Final
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from app.core.config.settings import get_settings

DEFAULT_STARTUP_TIMEOUT_SECONDS: Final[float] = 30.0
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS: Final[float] = 15.0


@dataclass(frozen=True, slots=True)
class ServeOptions:
    host: str
    port: int
    ssl_certfile: str | None
    ssl_keyfile: str | None


@dataclass(frozen=True, slots=True)
class RuntimeMetadata:
    pid: int
    host: str
    port: int
    log_file: str


def default_runtime_dir() -> Path:
    return get_settings().encryption_key_file.parent


def default_pid_file() -> Path:
    return default_runtime_dir() / "server.pid"


def default_log_file() -> Path:
    return default_runtime_dir() / "server.log"


def build_serve_command(python_executable: str, options: ServeOptions) -> list[str]:
    command = [
        python_executable,
        "-m",
        "app.cli",
        "serve",
        "--host",
        options.host,
        "--port",
        str(options.port),
    ]
    if options.ssl_certfile:
        command.extend(["--ssl-certfile", options.ssl_certfile])
    if options.ssl_keyfile:
        command.extend(["--ssl-keyfile", options.ssl_keyfile])
    return command


def read_runtime_metadata(pid_file: Path) -> RuntimeMetadata | None:
    resolved = pid_file.expanduser()
    if not resolved.exists():
        return None
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    return RuntimeMetadata(
        pid=int(payload["pid"]),
        host=str(payload["host"]),
        port=int(payload["port"]),
        log_file=str(payload["log_file"]),
    )


def write_runtime_metadata(pid_file: Path, metadata: RuntimeMetadata) -> None:
    resolved = pid_file.expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(asdict(metadata), indent=2, sort_keys=True), encoding="utf-8")


def remove_runtime_metadata(pid_file: Path) -> None:
    pid_file.expanduser().unlink(missing_ok=True)


def is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_running_metadata(pid_file: Path) -> tuple[RuntimeMetadata | None, bool]:
    metadata = read_runtime_metadata(pid_file)
    if metadata is None:
        return None, False
    if is_process_running(metadata.pid):
        return metadata, False
    remove_runtime_metadata(pid_file)
    return None, True


def start_background_server(
    options: ServeOptions,
    *,
    pid_file: Path,
    log_file: Path,
    startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
) -> RuntimeMetadata:
    existing, stale = load_running_metadata(pid_file)
    if existing is not None:
        raise RuntimeError(
            f"background server already running with pid {existing.pid} "
            f"on {existing.host}:{existing.port} (pid file: {pid_file.expanduser()})"
        )
    if stale:
        remove_runtime_metadata(pid_file)

    resolved_pid_file = pid_file.expanduser()
    resolved_log_file = log_file.expanduser()
    resolved_pid_file.parent.mkdir(parents=True, exist_ok=True)
    resolved_log_file.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)

    with resolved_log_file.open("ab") as log_handle:
        if os.name == "nt":
            process = subprocess.Popen(
                build_serve_command(sys.executable, options),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                cwd=os.getcwd(),
                env=env,
                creationflags=creationflags,
            )
        else:
            process = subprocess.Popen(
                build_serve_command(sys.executable, options),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                cwd=os.getcwd(),
                env=env,
                start_new_session=True,
            )

    metadata = RuntimeMetadata(
        pid=process.pid,
        host=options.host,
        port=options.port,
        log_file=str(resolved_log_file),
    )
    write_runtime_metadata(resolved_pid_file, metadata)

    if not wait_for_server_ready(
        metadata,
        timeout_seconds=startup_timeout_seconds,
        poll_process=lambda: process.poll(),
    ):
        terminate_process(metadata.pid)
        remove_runtime_metadata(resolved_pid_file)
        raise RuntimeError(f"background server failed readiness checks; see log file {resolved_log_file}")

    return metadata


def wait_for_server_ready(
    metadata: RuntimeMetadata,
    *,
    timeout_seconds: float,
    poll_process: Callable[[], int | None],
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    health_url = _healthcheck_url(metadata.host, metadata.port)
    while time.monotonic() < deadline:
        if poll_process() is not None:
            return False
        if healthcheck_ready(health_url):
            return True
        time.sleep(0.2)
    return False


def healthcheck_ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=0.5) as response:  # noqa: S310 - local readiness probe only
            return response.status == 200
    except (HTTPError, URLError, TimeoutError):
        return False


def shutdown_background_server(
    pid_file: Path,
    *,
    timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
) -> RuntimeMetadata | None:
    metadata, stale = load_running_metadata(pid_file)
    if metadata is None:
        if stale:
            return None
        return None

    terminate_process(metadata.pid)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not is_process_running(metadata.pid):
            remove_runtime_metadata(pid_file)
            return metadata
        time.sleep(0.2)

    raise RuntimeError(f"timed out waiting for pid {metadata.pid} to stop (pid file: {pid_file.expanduser()})")


def terminate_process(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def _healthcheck_url(host: str, port: int) -> str:
    probe_host = host
    if host in {"0.0.0.0", ""}:
        probe_host = "127.0.0.1"
    elif host == "::":
        probe_host = "::1"

    if ":" in probe_host and not probe_host.startswith("["):
        probe_host = f"[{probe_host}]"
    return f"http://{probe_host}:{port}/health/live"
