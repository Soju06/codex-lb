from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_WRAPPER = _REPOSITORY_ROOT / ".agents/skills/codex-review-loop/scripts/codex-subagent.sh"


@pytest.mark.parametrize(
    ("review_target", "expected_target"),
    [
        pytest.param(("--base", "origin/main"), ["--base", "origin/main"], id="base"),
        pytest.param(("--commit", "deadbeef"), ["--commit", "deadbeef"], id="commit"),
        pytest.param(("--uncommitted",), ["--uncommitted"], id="uncommitted"),
    ],
)
def test_codex_review_wrapper_forwards_supported_arguments_without_removed_flags(
    tmp_path: Path,
    review_target: tuple[str, ...],
    expected_target: list[str],
) -> None:
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    args_path = tmp_path / "codex-args"
    mock_codex = mock_bin / "codex"
    mock_codex.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\0' \"$@\" > \"$MOCK_CODEX_ARGS_PATH\"\nprintf 'codex\\nreview clean\\n'\n",
        encoding="utf-8",
    )
    mock_codex.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "CODEX_REVIEW_MODEL": "review-model",
            "CODEX_REVIEW_REASONING": "high",
            "MOCK_CODEX_ARGS_PATH": str(args_path),
            "PATH": f"{mock_bin}{os.pathsep}{env['PATH']}",
        }
    )

    result = subprocess.run(
        [str(_WRAPPER), *review_target],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "review clean\n"
    forwarded_args = args_path.read_bytes().rstrip(b"\0").decode().split("\0")
    assert forwarded_args == [
        "exec",
        "review",
        *expected_target,
        "-m",
        "review-model",
        "-c",
        'model_reasoning_effort="high"',
    ]
    assert "--ephemeral" not in forwarded_args
    assert "--full-auto" not in forwarded_args
