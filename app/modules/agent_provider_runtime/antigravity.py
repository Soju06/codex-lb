from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class AntigravityHarnessValidationError(Exception):
    pass


class AntigravityHarnessExecutionError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AntigravityHarnessRequest:
    prompt: str
    workspace_path: str
    timeout_seconds: int = 300
    add_dirs: tuple[str, ...] = ()
    conversation_id: str | None = None
    continue_conversation: bool = False
    sandbox: str | None = None


@dataclass(frozen=True, slots=True)
class AntigravityHarnessCommand:
    executable: str
    args: tuple[str, ...]
    cwd: Path
    timeout_seconds: int
    display_args: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AntigravityProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class AntigravityProcessRunnerPort(Protocol):
    async def run(
        self,
        command: AntigravityHarnessCommand,
        *,
        env: Mapping[str, str],
    ) -> AntigravityProcessResult: ...


class AntigravitySubprocessRunner:
    async def run(
        self,
        command: AntigravityHarnessCommand,
        *,
        env: Mapping[str, str],
    ) -> AntigravityProcessResult:
        started_at = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            command.executable,
            *command.args,
            cwd=str(command.cwd),
            env=dict(env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=command.timeout_seconds + 5,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise AntigravityHarnessExecutionError("Antigravity CLI timed out") from exc
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        return AntigravityProcessResult(
            exit_code=process.returncode or 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=max(0, int((time.perf_counter() - started_at) * 1000)),
        )


def build_antigravity_command(
    request: AntigravityHarnessRequest,
    *,
    executable: str = "agy",
) -> AntigravityHarnessCommand:
    prompt = request.prompt.strip()
    if not prompt:
        raise AntigravityHarnessValidationError("prompt is required")
    if request.timeout_seconds < 1 or request.timeout_seconds > 1800:
        raise AntigravityHarnessValidationError("timeout_seconds must be between 1 and 1800")
    if request.conversation_id is not None and request.continue_conversation:
        raise AntigravityHarnessValidationError("conversation_id cannot be combined with continue_conversation")

    workspace_path = _existing_directory(request.workspace_path, base=None, field_name="workspace_path")
    add_dirs = tuple(
        _existing_directory(path, base=workspace_path, field_name="add_dirs").resolve() for path in request.add_dirs
    )

    args: list[str] = [
        "--print",
        "--print-timeout",
        f"{request.timeout_seconds}s",
        "--prompt",
        prompt,
    ]
    display_args: list[str] = [
        "--print",
        "--print-timeout",
        f"{request.timeout_seconds}s",
        "--prompt",
        "<redacted>",
    ]
    if request.conversation_id is not None:
        conversation_id = request.conversation_id.strip()
        if not conversation_id:
            raise AntigravityHarnessValidationError("conversation_id cannot be blank")
        args.extend(["--conversation", conversation_id])
        display_args.extend(["--conversation", conversation_id])
    elif request.continue_conversation:
        args.append("--continue")
        display_args.append("--continue")
    for add_dir in add_dirs:
        args.extend(["--add-dir", str(add_dir)])
        display_args.extend(["--add-dir", str(add_dir)])
    if request.sandbox is not None:
        sandbox = request.sandbox.strip()
        if not sandbox:
            raise AntigravityHarnessValidationError("sandbox cannot be blank")
        args.extend(["--sandbox", sandbox])
        display_args.extend(["--sandbox", sandbox])

    _reject_dangerous_permission_flag(args)
    return AntigravityHarnessCommand(
        executable=executable,
        args=tuple(args),
        cwd=workspace_path.resolve(),
        timeout_seconds=request.timeout_seconds,
        display_args=tuple(display_args),
    )


def antigravity_harness_env(
    base_env: Mapping[str, str] | None = None,
    *,
    profile_id: str | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
    if profile_id is not None and profile_id.strip():
        env["AGY_CLI_PROFILE"] = profile_id.strip()
        env["ANTIGRAVITY_CLI_PROFILE"] = profile_id.strip()
    return env


def command_preview(command: AntigravityHarnessCommand) -> tuple[str, ...]:
    return (command.executable, *command.display_args)


def _existing_directory(path_value: str, *, base: Path | None, field_name: str) -> Path:
    raw = path_value.strip()
    if not raw:
        raise AntigravityHarnessValidationError(f"{field_name} cannot be blank")
    path = Path(raw)
    if not path.is_absolute():
        if base is None:
            raise AntigravityHarnessValidationError(f"{field_name} must be absolute")
        path = base / path
    if not path.is_dir():
        raise AntigravityHarnessValidationError(f"{field_name} must be an existing directory")
    return path


def _reject_dangerous_permission_flag(args: Sequence[str]) -> None:
    if "--dangerously-skip-permissions" in args:
        raise AntigravityHarnessValidationError("dangerous Antigravity permission bypass is not allowed")
