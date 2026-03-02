import os
import subprocess
import sys


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


if __name__ == '__main__':
    run([sys.executable, '-m', 'app.db.migrate', 'upgrade'])
    os.environ['CODEX_LB_DATABASE_MIGRATE_ON_STARTUP'] = 'false'
    os.execvp('fastapi', ['fastapi', 'run', 'app/main.py', '--host', '0.0.0.0', '--port', '2455'])
