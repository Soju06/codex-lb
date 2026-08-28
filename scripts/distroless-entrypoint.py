import os
import subprocess
import sys


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    run([sys.executable, "-m", "app.db.migrate", "upgrade"])
    os.environ["CODEX_LB_DATABASE_MIGRATE_ON_STARTUP"] = "false"
    # app.cli, not `fastapi run`: the CLI wires ws_max_size (websocket ingress
    # budget) and timeout_keep_alive into uvicorn, matching docker-entrypoint.sh.
    os.execv(sys.executable, [sys.executable, "-m", "app.cli", "--host", "0.0.0.0", "--port", "2455"])
