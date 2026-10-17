from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_checker_module() -> ModuleType:
    script_path = REPO_ROOT / "scripts" / "check_cancellation_safety.py"
    spec = importlib.util.spec_from_file_location("check_cancellation_safety", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.modules.get(spec.name) is module:
            del sys.modules[spec.name]
    return module


def _write_fixture(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("handler", "expected_line"),
    [
        ("except asyncio.CancelledError:\n            continue", 7),
        ("except BaseException:\n            continue", 7),
        ("except:\n            continue", 7),
    ],
)
def test_rejects_basic_cancellation_shield_retry(
    tmp_path: Path,
    handler: str,
    expected_line: int,
) -> None:
    checker = _load_checker_module()
    path = _write_fixture(
        tmp_path / "unsafe.py",
        f"""
import asyncio

async def wait_for_child(task):
    while True:
        try:
            return await asyncio.shield(task)
        {handler}
""",
    )

    assert [(item.path, item.line) for item in checker.find_violations(path)] == [(path, expected_line)]


_UNSAFE_CASES = [
    pytest.param(
        """
import asyncio as aio
async def wait(task):
    while True:
        try:
            return await aio.shield(task)
        except aio.CancelledError:
            continue
""",
        6,
        id="module-alias",
    ),
    pytest.param(
        """
from asyncio import CancelledError, shield as preserve
async def wait(task):
    while True:
        try:
            guarded = preserve(task)
            await guarded
        except CancelledError:
            continue
""",
        6,
        id="imported-assigned-alias",
    ),
    pytest.param(
        """
import asyncio
async def wait(task):
    while True:
        guarded = asyncio.shield(task)
        try:
            await guarded
        except asyncio.CancelledError:
            continue
""",
        5,
        id="assignment-before-try",
    ),
    pytest.param(
        """
import asyncio
async def wait(task, timeout):
    while True:
        guarded = asyncio.shield(task)
        try:
            await asyncio.wait_for(guarded, timeout)
        except asyncio.CancelledError:
            continue
""",
        5,
        id="assigned-shield-indirect-await",
    ),
    pytest.param(
        """
import asyncio
import anyio
async def wait(task):
    with anyio.CancelScope(shield=True):
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
""",
        8,
        id="anyio-shield-is-not-exemption",
    ),
    pytest.param(
        """
import asyncio
async def wait(task, retry):
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if retry:
                continue
            raise
""",
        6,
        id="conditional-continue",
    ),
    pytest.param(
        """
import asyncio
async def wait(task, retry):
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                if retry:
                    continue
            finally:
                task.exception()
            raise
""",
        6,
        id="try-finally-continue",
    ),
    pytest.param(
        """
import asyncio
async def wait(task):
    while True:
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                break
""",
        7,
        id="break-is-conservative",
    ),
    pytest.param(
        """
import asyncio
async def wait(task):
    while True:
        try:
            return await asyncio.shield(task)
        except* asyncio.CancelledError:
            pass
""",
        6,
        id="exception-group-handler",
    ),
    pytest.param(
        """
import asyncio
async def wait(task, retry):
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            match retry:
                case True:
                    continue
                case _:
                    pass
            raise
""",
        6,
        id="match-continue",
    ),
]


@pytest.mark.parametrize(("source", "expected_line"), _UNSAFE_CASES)
def test_rejects_structural_variants(tmp_path: Path, source: str, expected_line: int) -> None:
    checker = _load_checker_module()
    path = _write_fixture(tmp_path / "unsafe_variant.py", source)

    assert [(item.path, item.line) for item in checker.find_violations(path)] == [(path, expected_line)]


_SAFE_CASES = [
    pytest.param(
        """
import asyncio
async def wait(task, fail):
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if fail:
                raise RuntimeError
            return None
""",
        id="all-conditional-paths-terminate",
    ),
    pytest.param(
        """
import asyncio
async def wait(task):
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                raise
            finally:
                task.exception()
""",
        id="terminal-raise-through-finally",
    ),
    pytest.param(
        """
import asyncio
async def wait(task):
    while True:
        try:
            async def helper():
                return await asyncio.shield(task)
            return await task
        except asyncio.CancelledError:
            continue
""",
        id="nested-function-shield",
    ),
    pytest.param(
        """
def unrelated():
    from asyncio import shield as preserve
    return preserve
async def wait(task, preserve):
    while True:
        try:
            return await preserve(task)
        except BaseException:
            continue
""",
        id="nested-import-does-not-leak",
    ),
    pytest.param(
        """
import asyncio
async def wait(task, asyncio):
    while True:
        try:
            return await asyncio.shield(task)
        except BaseException:
            continue
""",
        id="module-parameter-shadow",
    ),
    pytest.param(
        """
import asyncio
async def wait(task, BaseException):
    while True:
        try:
            return await asyncio.shield(task)
        except BaseException:
            continue
""",
        id="exception-parameter-shadow",
    ),
    pytest.param(
        """
import asyncio
from app.core.utils.shared_future import wait_on_shared_future
async def wait(task):
    while True:
        try:
            return await wait_on_shared_future(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
""",
        id="canonical-shared-future-wait",
    ),
    pytest.param(
        """
import asyncio
async def wait(task):
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
""",
        id="single-shield-propagates",
    ),
]


@pytest.mark.parametrize("source", _SAFE_CASES)
def test_allows_non_retrying_or_unrelated_variants(tmp_path: Path, source: str) -> None:
    checker = _load_checker_module()
    path = _write_fixture(tmp_path / "safe_variant.py", source)

    assert checker.find_violations(path) == []


def test_repository_cancellation_safety_passes() -> None:
    checker = _load_checker_module()

    assert checker.repository_violations() == []


def test_main_reports_violations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _load_checker_module()
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    _write_fixture(
        app_dir / "unsafe.py",
        """
import asyncio
async def wait(task):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass
""",
    )
    monkeypatch.setattr(checker, "APP_DIR", app_dir)
    monkeypatch.setattr(checker, "ROOT", tmp_path)

    assert checker.main() == 1
    assert capsys.readouterr().err == (
        "cancellation safety check failed: app/unsafe.py:6: "
        "cancellation-catching loop retries asyncio.shield; use wait_on_shared_future\n"
    )


_DIRECT_AWAIT_REASON = (
    "direct await of a task that may defer cancellation; "
    "await it through wait_on_shared_future or _await_task_deferring_cancellation"
)

_UNSAFE_DIRECT_AWAIT_CASES = [
    pytest.param(
        """
import asyncio
from app.core.utils.shared_future import wait_on_shared_future
async def inject(source):
    iterator = source.__aiter__()

    async def _next_chunk(it):
        return await it.__anext__()

    pending = asyncio.create_task(_next_chunk(iterator))
    try:
        yield await wait_on_shared_future(pending, timeout=1.0)
    finally:
        if not pending.done():
            pending.cancel()
            try:
                await pending
            except BaseException:
                pass
""",
        17,
        id="iterator-step-task-awaited-in-finally",
    ),
    pytest.param(
        """
import asyncio
async def _read_first(stream):
    return await anext(stream)

async def probe(stream):
    first_task = asyncio.create_task(_read_first(stream))
    return await first_task
""",
        8,
        id="anext-task-bare-await",
    ),
    pytest.param(
        """
import asyncio
async def prepend(first_task: asyncio.Task[str], stream):
    first = await first_task
    yield first
""",
        4,
        id="task-parameter-bare-await",
    ),
    pytest.param(
        """
import asyncio
from app.core.utils.shared_future import _await_cleanup_deferring_cancellation as defer_cleanup
async def cleanup_owner(inner):
    await defer_cleanup(inner)

async def caller(inner):
    owner = asyncio.create_task(cleanup_owner(inner))
    await owner
""",
        9,
        id="deferring-helper-task-bare-await",
    ),
    pytest.param(
        """
import asyncio
async def consume(source):
    async for item in source:
        return item

async def caller(scheduler, source):
    task = scheduler.create_task(consume(source))
    return await task
""",
        9,
        id="async-for-task-via-scheduler-factory",
    ),
    pytest.param(
        """
async def probe(stream):
    first_task = _create_first_stream_probe_task(stream)
    return await first_task
""",
        4,
        id="probe-factory-bare-await",
    ),
]


@pytest.mark.parametrize(("source", "expected_line"), _UNSAFE_DIRECT_AWAIT_CASES)
def test_rejects_direct_await_of_deferring_task(tmp_path: Path, source: str, expected_line: int) -> None:
    checker = _load_checker_module()
    path = _write_fixture(tmp_path / "unsafe_await.py", source)

    assert [(item.line, item.reason) for item in checker.find_violations(path)] == [
        (expected_line, _DIRECT_AWAIT_REASON)
    ]


_SAFE_DIRECT_AWAIT_CASES = [
    pytest.param(
        """
import asyncio
from app.core.utils.shared_future import _await_task_deferring_cancellation, wait_on_shared_future
async def inject(source):
    iterator = source.__aiter__()

    async def _next_chunk(it):
        return await it.__anext__()

    pending = asyncio.create_task(_next_chunk(iterator))
    try:
        yield await wait_on_shared_future(pending, timeout=1.0)
    finally:
        if not pending.done():
            pending.cancel()
            try:
                await _await_task_deferring_cancellation(pending)
            except BaseException:
                pass
""",
        id="proxy-and-helper-waits",
    ),
    pytest.param(
        """
import asyncio
from app.core.utils.shared_future import _await_task_deferring_cancellation, wait_on_shared_future
async def prepend(first_task: asyncio.Task[str], stream):
    try:
        first = await wait_on_shared_future(first_task)
    finally:
        if not first_task.done():
            first_task.cancel()
            await _await_task_deferring_cancellation(first_task)
    yield first
""",
        id="task-parameter-through-proxy",
    ),
    pytest.param(
        """
import asyncio
async def _next_chunk(it):
    return await it.__anext__()

async def read(iterator, timeout):
    next_chunk = asyncio.create_task(_next_chunk(iterator))
    done, _ = await asyncio.wait({next_chunk}, timeout=timeout)
    if not done:
        raise TimeoutError
    return await next_chunk
""",
        id="settled-through-asyncio-wait",
    ),
    pytest.param(
        """
import asyncio
async def shutdown_body():
    await asyncio.sleep(0)

async def shutdown():
    shutdown_task = asyncio.create_task(shutdown_body())
    await shutdown_task
""",
        id="plain-task-without-iterator-or-deferral",
    ),
    pytest.param(
        """
import asyncio
async def refresh(task: asyncio.Task[int]) -> int:
    return await asyncio.shield(task)
""",
        id="task-parameter-single-shield",
    ),
]


@pytest.mark.parametrize("source", _SAFE_DIRECT_AWAIT_CASES)
def test_allows_proxied_settled_or_plain_task_awaits(tmp_path: Path, source: str) -> None:
    checker = _load_checker_module()
    path = _write_fixture(tmp_path / "safe_await.py", source)

    assert checker.find_violations(path) == []


def test_main_reports_direct_await_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _load_checker_module()
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    _write_fixture(
        app_dir / "unsafe.py",
        """
async def prepend(first_task: asyncio.Task[str]):
    return await first_task
""",
    )
    monkeypatch.setattr(checker, "APP_DIR", app_dir)
    monkeypatch.setattr(checker, "ROOT", tmp_path)

    assert checker.main() == 1
    assert capsys.readouterr().err == f"cancellation safety check failed: app/unsafe.py:3: {_DIRECT_AWAIT_REASON}\n"


_UNSAFE_UNPROVEN_SETTLEMENT_CASES = [
    pytest.param(
        """
import asyncio
async def _next_chunk(it):
    return await it.__anext__()

async def read(iterator):
    task = asyncio.create_task(_next_chunk(iterator))
    done, _ = await asyncio.wait({task}, timeout=1.0)
    return await task
""",
        9,
        id="timeout-without-done-guard",
    ),
    pytest.param(
        """
import asyncio
async def _next_chunk(it):
    return await it.__anext__()

async def read(iterator, other):
    task = asyncio.create_task(_next_chunk(iterator))
    done, _ = await asyncio.wait({task, other}, return_when=asyncio.FIRST_COMPLETED)
    return await task
""",
        9,
        id="first-completed-without-membership-proof",
    ),
    pytest.param(
        """
import asyncio
async def _next_chunk(it):
    return await it.__anext__()

async def read(owner, iterator):
    task = asyncio.create_task(_next_chunk(iterator))
    done, _ = await owner.wait({task})
    if not done:
        raise TimeoutError
    return await task
""",
        11,
        id="non-asyncio-wait-is-not-a-proof",
    ),
    pytest.param(
        """
import asyncio
async def _next_chunk(it):
    return await it.__anext__()

async def read(iterator):
    task = asyncio.create_task(_next_chunk(iterator))
    first = await task
    done, _ = await asyncio.wait({task})
    return first
""",
        8,
        id="await-before-the-wait",
    ),
    pytest.param(
        """
import asyncio
from app.core.utils.shared_future import _await_task_deferring_cancellation

async def worker(inner):
    await _await_task_deferring_cancellation(inner)

async def caller(inner):
    task = asyncio.create_task(worker(inner))
    await task
""",
        10,
        id="module-level-deferring-worker",
    ),
]


@pytest.mark.parametrize(("source", "expected_line"), _UNSAFE_UNPROVEN_SETTLEMENT_CASES)
def test_rejects_direct_await_without_settlement_proof(tmp_path: Path, source: str, expected_line: int) -> None:
    checker = _load_checker_module()
    path = _write_fixture(tmp_path / "unproven.py", source)

    assert [(item.line, item.reason) for item in checker.find_violations(path)] == [
        (expected_line, _DIRECT_AWAIT_REASON)
    ]


_SAFE_PROVEN_SETTLEMENT_CASES = [
    pytest.param(
        """
import asyncio
async def _next_chunk(it):
    return await it.__anext__()

async def read(iterator):
    task = asyncio.create_task(_next_chunk(iterator))
    done, _ = await asyncio.wait({task}, timeout=1.0)
    if task in done:
        return await task
    task.cancel()
""",
        id="membership-guard",
    ),
    pytest.param(
        """
from asyncio import create_task, wait, FIRST_COMPLETED
async def consume(source):
    async for item in source:
        return item

async def drain(sources):
    pending = {create_task(consume(source)) for source in sources}
    while pending:
        completed, pending = await wait(pending, return_when=FIRST_COMPLETED)
        for task in completed:
            await task
""",
        id="iteration-over-done-set",
    ),
    pytest.param(
        """
import asyncio
from app.core.utils.shared_future import _await_task_deferring_cancellation

async def worker(inner):
    await _await_task_deferring_cancellation(inner)

async def caller(inner):
    async def worker(inner):
        await inner

    task = asyncio.create_task(worker(inner))
    await task
""",
        id="local-non-deferring-worker-shadows-module-worker",
    ),
]


@pytest.mark.parametrize("source", _SAFE_PROVEN_SETTLEMENT_CASES)
def test_allows_proven_settlement_and_lexical_shadowing(tmp_path: Path, source: str) -> None:
    checker = _load_checker_module()
    path = _write_fixture(tmp_path / "proven.py", source)

    assert checker.find_violations(path) == []
