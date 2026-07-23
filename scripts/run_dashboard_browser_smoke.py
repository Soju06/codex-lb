from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
STARTUP_TIMEOUT_SECONDS = 90.0
SHUTDOWN_TIMEOUT_SECONDS = 30.0


def _run_backend(listener_fd: int) -> None:
    # This subprocess deliberately changes only its Settings class before the
    # application is imported. The smoke harness must not inherit a developer's
    # repository-local .env or .env.local database/auth configuration.
    from app.core.config import settings as settings_module

    empty_env_file = Path(os.environ["CODEX_LB_DATA_DIR"]) / ".dashboard-browser-smoke.env"
    settings_module.ENV_FILES = (empty_env_file, empty_env_file)
    settings_module.Settings.model_config["env_file"] = None

    import uvicorn

    uvicorn.run("app.main:app", fd=listener_fd, log_level="warning")


def _smoke_environment(data_dir: Path) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("CODEX_LB_")}
    environment.update(
        {
            "CODEX_LB_DATA_DIR": str(data_dir),
            "CODEX_LB_UPSTREAM_BASE_URL": "http://127.0.0.1:9/backend-api",
            "CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV": "false",
            "CODEX_LB_USAGE_REFRESH_ENABLED": "false",
            "CODEX_LB_LIVE_USAGE_INGESTION_ENABLED": "false",
            "CODEX_LB_MODEL_REGISTRY_ENABLED": "false",
            "CODEX_LB_STICKY_SESSION_CLEANUP_ENABLED": "false",
            "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED": "false",
            "CODEX_LB_QUOTA_PLANNER_SCHEDULER_ENABLED": "false",
            "CODEX_LB_AUTOMATIONS_SCHEDULER_ENABLED": "false",
            "CODEX_LB_AUTH_GUARDIAN_ENABLED": "false",
            "CODEX_LB_METRICS_ENABLED": "false",
            "CODEX_LB_OTEL_ENABLED": "false",
            # Always-on database maintenance loops remain harmless because the
            # isolated database starts empty; every configurable external loop
            # is disabled above.
            # Suppress first-run token logging while keeping standard localhost
            # authentication active. The generated value never leaves this process tree.
            "CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN": secrets.token_urlsafe(32),
        }
    )
    return environment


def _reserve_loopback_socket() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.set_inheritable(True)
    return listener


def _wait_until_ready(server: subprocess.Popen[bytes], base_url: str) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        return_code = server.poll()
        if return_code is not None:
            raise RuntimeError(f"dashboard smoke backend exited during startup with code {return_code}")
        try:
            with opener.open(f"{base_url}/health", timeout=1.0) as response:
                if response.status == 200:
                    return
        except (TimeoutError, urllib.error.URLError):
            time.sleep(0.1)
    raise TimeoutError(f"dashboard smoke backend was not ready within {STARTUP_TIMEOUT_SECONDS:.0f}s")


def _stop_server(server: subprocess.Popen[bytes]) -> None:
    if server.poll() is not None:
        return
    try:
        os.killpg(server.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        server.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(server.pid, signal.SIGKILL)
        server.wait(timeout=5.0)


def run() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-lb-dashboard-browser-smoke-") as temporary_dir:
        data_dir = Path(temporary_dir)
        listener = _reserve_loopback_socket()
        port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        environment = _smoke_environment(data_dir)
        try:
            server = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--backend-fd",
                    str(listener.fileno()),
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                pass_fds=(listener.fileno(),),
                start_new_session=True,
            )
        finally:
            listener.close()
        try:
            _wait_until_ready(server, base_url)
            playwright_environment = environment | {
                "CODEX_LB_BROWSER_SMOKE_BASE_URL": base_url,
                "CODEX_LB_BROWSER_SMOKE_OUTPUT_DIR": str(data_dir / "playwright-output"),
            }
            playwright_environment.pop("CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN")
            completed = subprocess.run(
                ["bun", "run", "test:browser-smoke"],
                cwd=FRONTEND_ROOT,
                env=playwright_environment,
                check=False,
            )
            return completed.returncode
        finally:
            _stop_server(server)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--backend-fd":
        _run_backend(int(sys.argv[2]))
        raise SystemExit(0)
    raise SystemExit(run())
