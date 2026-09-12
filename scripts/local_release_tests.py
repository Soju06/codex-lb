"""Run selected regressions against a release, without editing that release."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.local_verification_runtime import Report, VerificationError, child


def fixture(repo: Path, ref: str) -> tuple[str, bytes]:
    try:
        revision = (
            subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"],
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            .decode()
            .strip()
        )
        content = subprocess.check_output(
            ["git", "-C", str(repo), "show", revision + ":tests/conftest.py"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        raise VerificationError("Fixture reference cannot be resolved; no fixture was written") from None
    if not content.strip():
        raise VerificationError("Fixture baseline is empty")
    return revision, content


def python_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


async def test_release(report: Report, release: Path, repo: Path, ref: str, targets: list[str]) -> None:
    if report.path is None:
        raise VerificationError("Release tests require a report path")
    release, repo = release.resolve(strict=True), repo.resolve(strict=True)
    if not (release / "app/__init__.py").is_file():
        raise VerificationError("Release application package is missing")
    for target in targets:
        file = Path(target.split("::", 1)[0])
        if file.is_absolute() or ".." in file.parts or not file.parts or file.parts[0] != "tests":
            raise VerificationError("Test targets must be repository-relative paths under tests/")
        if not (repo / file).is_file():
            raise VerificationError("Test target does not exist")
    revision, content = await report.run(
        "fixture_baseline",
        lambda: asyncio.to_thread(fixture, repo, ref),
        lambda result: {"revision": result[0], "fixtureSha256": hashlib.sha256(result[1]).hexdigest()},
    )
    log = report.path.with_name(report.path.name + ".pytest.log").resolve()
    with tempfile.TemporaryDirectory(prefix="codex-lb-release-tests-") as directory:
        root = Path(directory)
        shutil.copytree(repo / "tests", root / "tests", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        # Fixture resolution succeeded before any write; source and release are untouched.
        (root / "tests/conftest.py").write_bytes(content)
        shutil.copy2(repo / "pyproject.toml", root / "pyproject.toml")
        release_digest = python_digest(release / "app")
        tests_digest = python_digest(root / "tests")
        provenance = root / "import-provenance.json"
        isolated_db = "sqlite+aiosqlite:///" + str(root / "test.db")
        env = {key: value for key, value in os.environ.items() if not key.startswith(("CODEX_LB_", "PYTEST_"))}
        env.update(
            CODEX_LB_DATABASE_URL=isolated_db,
            CODEX_LB_TEST_DATABASE_URL=isolated_db,
            CODEX_LB_ENCRYPTION_KEY_FILE=str(root / "test.key"),
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        )
        # -I ignores inherited PYTHONPATH and the working checkout. Sandbox tests
        # precede candidate tests, but only the candidate supplies application code.
        code = """import json,sys
from pathlib import Path
root,release,provenance,*targets=sys.argv[1:]
sys.path[:0]=[root,release]
import app
actual=Path(app.__file__).resolve()
if actual != Path(release,'app/__init__.py').resolve():
    raise RuntimeError('Application imported from the wrong release')
Path(provenance).write_text(json.dumps({'applicationFile':str(actual)}))
import pytest
raise SystemExit(pytest.main([
    '-p','pytest_asyncio.plugin','-p','pytest_timeout','--import-mode=importlib',
    '-q','--tb=short','--show-capture=no',*targets]))
"""

        async def run() -> dict[str, str | int]:
            exit_code = await child(
                [sys.executable, "-I", "-B", "-c", code, str(root), str(release), str(provenance), *targets],
                cwd=root,
                env=env,
                log=log,
            )
            if exit_code != 0:
                raise VerificationError(f"Release tests failed (exit {exit_code}); inspect the private pytest log")
            if python_digest(release / "app") != release_digest:
                raise VerificationError("Release application changed during verification")
            if not provenance.is_file():
                raise VerificationError("Release import provenance was not produced")
            return {
                **json.loads(provenance.read_text()),
                "fixtureRevision": revision,
                "log": str(log),
                "exitCode": exit_code,
            }

        # Persist the log location even when the child fails or times out.
        print(json.dumps({"stage": "release_tests", "log": str(log)}), flush=True)
        await report.run(
            "release_tests",
            run,
            lambda result: result,
            initial={
                "log": str(log),
                "release": str(release),
                "fixtureRevision": revision,
                "targets": targets,
                "releasePythonSha256": release_digest,
                "testsPythonSha256": tests_digest,
            },
        )
