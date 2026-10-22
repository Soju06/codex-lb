"""Measure real built-page completion on an isolated synthetic SQLite workload.

Run this file with the same Python/dependencies for both source roots. Build each
frontend first. No production database, credentials or provider traffic is used.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _settings_without_dotenv() -> None:
    from app.core.config import settings

    settings.ENV_FILES = ()
    settings.Settings.model_config["env_file"] = None


def _seed() -> None:
    _settings_without_dotenv()
    from sqlalchemy import create_engine, insert, select, update

    from app.core.config.settings import get_settings
    from app.core.crypto import TokenEncryptor
    from app.db.migrate import run_upgrade
    from app.db.migration_url import to_sync_database_url
    from app.db.models import Account, DashboardSettings, RequestLog, UsageHistory

    settings = get_settings()
    assert settings.database_url.startswith("sqlite"), "Only a new benchmark SQLite database is allowed"
    run_upgrade(settings.database_url, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(settings.database_url))
    now = datetime.fromisoformat(os.environ["BENCHMARK_NOW"])
    encrypted = TokenEncryptor().encrypt("synthetic-unused-token")
    with engine.begin() as connection:
        assert connection.execute(Account.__table__.select()).first() is None
        # This is an established installation, not the first-run consent flow.
        # All telemetry loops remain disabled; no opt-out network call is made.
        if connection.execute(select(DashboardSettings.id)).first() is None:
            connection.execute(insert(DashboardSettings), {"id": 1, "telemetry_consent": "disabled"})
        else:
            connection.execute(update(DashboardSettings).values(telemetry_consent="disabled"))
        connection.execute(
            insert(Account),
            [
                {
                    "id": f"benchmark-{index:02d}",
                    "email": f"account-{index:02d}@example.com",
                    "alias": f"Benchmark account {index:02d}",
                    "plan_type": "pro",
                    "status": "active",
                    "access_token_encrypted": encrypted,
                    "refresh_token_encrypted": encrypted,
                    "id_token_encrypted": encrypted,
                    "last_refresh": now,
                }
                for index in range(24)
            ],
        )
        history = []
        for index in range(24):
            for sample in range(288):
                at = now - timedelta(minutes=(287 - sample) * 10)
                for window, minutes in (("primary", 300), ("secondary", 10080)):
                    history.append(
                        {
                            "account_id": f"benchmark-{index:02d}",
                            "recorded_at": at,
                            "window": window,
                            "window_minutes": minutes,
                            "used_percent": 5 + sample * 0.1,
                            "reset_at": int(
                                (now.replace(tzinfo=timezone.utc) + timedelta(minutes=minutes)).timestamp()
                            ),
                        }
                    )
        connection.execute(insert(UsageHistory), history)
        for offset in range(0, 50_000, 1000):
            connection.execute(
                insert(RequestLog),
                [
                    {
                        "account_id": f"benchmark-{index % 24:02d}",
                        "request_id": f"benchmark-request-{index}",
                        "model": "gpt-5.1",
                        "status": "success",
                        "input_tokens": 2000 + index % 100,
                        "output_tokens": 500 + index % 50,
                        "cached_input_tokens": 1000,
                        "cost_usd": 0.01,
                        "latency_ms": 2000,
                        "latency_first_token_ms": 200,
                        "requested_at": now - timedelta(seconds=(index * 47) % (28 * 86400)),
                        "conversation_id": f"benchmark-conversation-{index % 1000}",
                    }
                    for index in range(offset, offset + 1000)
                ],
            )
    engine.dispose()


def _ready(server: subprocess.Popen, base_url: str, snapshots: bool) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    path = "/health/read-snapshots" if snapshots else "/health"
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"Benchmark backend exited: {server.returncode}")
        try:
            with opener.open(base_url + path, timeout=2) as response:
                if response.status == 200:
                    return
        except (TimeoutError, urllib.error.URLError):
            time.sleep(0.1)
    raise TimeoutError(f"Backend did not become ready at {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed-from", type=Path)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.samples <= 20:
        parser.error("--samples must be between 1 and 20")
    root = args.source_root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    smoke = runpy.run_path(str(root / "scripts/run_dashboard_browser_smoke.py"))
    data = output / "data"
    data.mkdir()
    environment = smoke["_smoke_environment"](data)
    environment["PYTHONPATH"] = str(root)
    environment["BENCHMARK_NOW"] = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat()
    script = Path(__file__).resolve()
    if args.seed_from:
        shutil.copytree(args.seed_from, data, dirs_exist_ok=True)
    else:
        subprocess.run([sys.executable, str(script), "--seed"], cwd=root, env=environment, check=True)
        (data / "benchmark-clock.txt").write_text(environment["BENCHMARK_NOW"])
    environment["BENCHMARK_NOW"] = (data / "benchmark-clock.txt").read_text()
    # Preserve the identical seed before either application serves a request.
    shutil.copytree(data, output / "seed")
    snapshots = (root / "app/modules/read_snapshots").is_dir()
    results = []
    for index in range(args.samples):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        listener.set_inheritable(True)
        base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        log = (output / f"backend-{index}.log").open("wb")
        start = time.monotonic()
        server = subprocess.Popen(
            [sys.executable, str(script), "--backend", str(listener.fileno())],
            cwd=root,
            env=environment,
            pass_fds=(listener.fileno(),),
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        listener.close()
        try:
            _ready(server, base_url, snapshots)
            preparation_seconds = time.monotonic() - start
            # A second fresh browser context measures the same backend after its
            # process-local caches have been populated; no first-load-only claim.
            for phase in ("first", "repeat"):
                sample_output = output / f"{index}-{phase}"
                subprocess.run(
                    [
                        "bun",
                        str(script.parent.parent / "frontend/browser-smoke/page-completion.mjs"),
                        base_url,
                        str(sample_output),
                    ],
                    cwd=root / "frontend",
                    env=environment,
                    check=True,
                )
                results.append(
                    {
                        "sample": index,
                        "phase": phase,
                        "startupToReadinessSeconds": preparation_seconds,
                        "pages": json.loads((sample_output / "completion.json").read_text()),
                    }
                )
        finally:
            if server.poll() is None:
                os.killpg(server.pid, signal.SIGTERM)
                try:
                    server.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid, signal.SIGKILL)
                    server.wait(timeout=10)
                    raise RuntimeError("Benchmark backend exceeded graceful shutdown")
            log.close()
    (output / "results.json").write_text(
        json.dumps(
            {
                "sourceRoot": str(root),
                "snapshots": snapshots,
                "samples": results,
                "workload": {"accounts": 24, "requestLogs": 50_000, "quotaObservations": 13_824},
                "clock": "navigation through successful API bodies, React render and DOM stable for 300ms",
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    if sys.argv[1:] == ["--seed"]:
        _seed()
    elif len(sys.argv) == 3 and sys.argv[1] == "--backend":
        smoke = runpy.run_path(str(Path.cwd() / "scripts/run_dashboard_browser_smoke.py"))
        original_disable = smoke["_disable_background_loops"]

        def disable_and_pin_evaluation_clock(main_module):
            original_disable(main_module)
            clock = datetime.fromisoformat(os.environ["BENCHMARK_NOW"])
            for name, module in tuple(sys.modules.items()):
                if name.startswith("app.") and module is not None and hasattr(module, "utcnow"):
                    module.utcnow = lambda: clock

        smoke["_run_backend"].__globals__["_disable_background_loops"] = disable_and_pin_evaluation_clock
        smoke["_run_backend"](int(sys.argv[2]))
    else:
        main()
