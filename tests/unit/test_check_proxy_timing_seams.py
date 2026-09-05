from __future__ import annotations

import importlib.util
import sys
import textwrap
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_SPEC_BLOCK = """
[scheduler_kwarg_required]
_needs_scheduler = "scheduler"
_needs_both = ["scheduler", "clock"]

[allowances.timing]
"app/modules/proxy/allowed.py" = 1

[allowances.clock]
"app/modules/proxy/allowed.py" = 1
"""


def _load_checker_module() -> ModuleType:
    script_path = REPO_ROOT / "scripts" / "check_proxy_timing_seams.py"
    spec = importlib.util.spec_from_file_location("check_proxy_timing_seams", script_path)
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


def _write_module(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip("\n"), encoding="utf-8")
    return path


def _write_spec(path: Path, block: str = _SPEC_BLOCK) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# proxy-architecture Specification\n\n"
        "<!-- proxy-timing-seams:start -->\n```toml\n" + block.strip("\n") + "\n```\n"
        "<!-- proxy-timing-seams:end -->\n",
        encoding="utf-8",
    )


def _fixture_config(checker: ModuleType) -> object:
    return checker.Config(
        scheduler_kwarg_required={"_needs_scheduler": ("scheduler",), "_needs_both": ("scheduler", "clock")}
    )


def _configure_fixture(checker: ModuleType, root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    proxy_dir = root / "app" / "modules" / "proxy"
    proxy_dir.mkdir(parents=True)
    _write_module(
        proxy_dir / "allowed.py",
        """
        import asyncio
        import time

        async def run(coro):
            asyncio.create_task(coro)
            return time.monotonic()
        """,
    )
    shared_future = _write_module(root / "app" / "core" / "utils" / "shared_future.py", "VALUE = 1\n")
    spec_path = root / "openspec" / "specs" / "proxy-architecture" / "spec.md"
    _write_spec(spec_path)
    monkeypatch.setattr(checker, "ROOT", root)
    monkeypatch.setattr(checker, "PROXY_DIR", proxy_dir)
    monkeypatch.setattr(checker, "SHARED_FUTURE_PATH", shared_future)
    monkeypatch.setattr(checker, "PROXY_ARCHITECTURE_SPEC_PATH", spec_path)
    return proxy_dir


_RAW_CASES = [
    pytest.param(
        "import asyncio\nasync def f(delay):\n    await asyncio.sleep(delay)\n", [(3, "raw-sleep")], id="raw-sleep"
    ),
    pytest.param("import anyio\nasync def f():\n    await anyio.sleep(1)\n", [(3, "raw-sleep")], id="raw-sleep-anyio"),
    pytest.param(
        "import asyncio as aio\nasync def f():\n    await aio.sleep(0.1)\n",
        [(3, "raw-sleep")],
        id="raw-sleep-module-alias",
    ),
    pytest.param(
        "from asyncio import sleep as pause\nasync def f():\n    await pause(0.1)\n",
        [(3, "raw-sleep")],
        id="raw-sleep-imported-alias",
    ),
    pytest.param(
        "async def f():\n    import asyncio as nested\n    await nested.sleep(0.1)\n",
        [(3, "raw-sleep")],
        id="raw-sleep-nested-import-alias-detected",
    ),
    pytest.param(
        "import asyncio\nasync def f(fut):\n    await asyncio.wait_for(fut, timeout=1)\n",
        [(3, "raw-timeout")],
        id="raw-timeout-wait-for",
    ),
    pytest.param(
        "import asyncio\nasync def f(fs, t):\n    await asyncio.wait(fs, timeout=t)\n",
        [(3, "raw-timeout")],
        id="raw-timeout-timed-wait",
    ),
    pytest.param(
        "import asyncio\nasync def f():\n    async with asyncio.timeout(1):\n        pass\n",
        [(3, "raw-timeout")],
        id="raw-timeout-asyncio-timeout",
    ),
    pytest.param(
        "import asyncio\nasync def f(at):\n    async with asyncio.timeout_at(at):\n        pass\n",
        [(3, "raw-timeout")],
        id="raw-timeout-asyncio-timeout-at",
    ),
    pytest.param(
        "import anyio\nasync def f():\n    with anyio.fail_after(1):\n        pass\n",
        [(3, "raw-timeout")],
        id="raw-timeout-anyio-fail-after",
    ),
    pytest.param(
        "from anyio import move_on_after\nasync def f():\n    with move_on_after(1):\n        pass\n",
        [(3, "raw-timeout")],
        id="raw-timeout-anyio-move-on-after-imported",
    ),
    pytest.param(
        "from app.core.utils.shared_future import wait_on_shared_future\n"
        "async def f(shared, t):\n    return await wait_on_shared_future(shared, timeout=t)\n",
        [(3, "raw-timeout")],
        id="raw-timeout-shared-future-without-scheduler",
    ),
    pytest.param(
        "import asyncio\ndef f(coro):\n    return asyncio.create_task(coro)\n",
        [(3, "raw-task-spawn")],
        id="raw-task-spawn",
    ),
    pytest.param(
        "import asyncio\ndef f(coro):\n    return asyncio.ensure_future(coro)\n",
        [(3, "raw-task-spawn")],
        id="raw-task-spawn-ensure-future",
    ),
    pytest.param(
        "import asyncio\ndef f(coro):\n    return asyncio.get_running_loop().create_task(coro)\n",
        [(3, "raw-task-spawn")],
        id="raw-task-spawn-loop-chain",
    ),
    pytest.param(
        "import asyncio\ndef f(coro):\n    loop = asyncio.get_running_loop()\n    return loop.create_task(coro)\n",
        [(4, "raw-task-spawn")],
        id="raw-task-spawn-loop-alias",
    ),
    pytest.param(
        "import asyncio\nasync def f():\n    async with asyncio.TaskGroup() as tg:\n        pass\n",
        [(3, "raw-task-spawn")],
        id="raw-task-spawn-task-group",
    ),
    pytest.param(
        "import time\ndef f():\n    return time.monotonic()\n", [(3, "raw-clock-read")], id="raw-clock-read-monotonic"
    ),
    pytest.param("import time\ndef f():\n    return time.time()\n", [(3, "raw-clock-read")], id="raw-clock-read-time"),
    pytest.param(
        "import time\ndef f():\n    return time.perf_counter()\n",
        [(3, "raw-clock-read")],
        id="raw-clock-read-perf-counter",
    ),
    pytest.param(
        "from time import monotonic\ndef f():\n    return monotonic()\n",
        [(3, "raw-clock-read")],
        id="raw-clock-read-imported-member",
    ),
    pytest.param(
        "import time\ndef f(now=None):\n    return time.time() if now is None else now\n",
        [(3, "raw-clock-read")],
        id="raw-clock-read-default-idiom",
    ),
    pytest.param(
        "import asyncio\ndef f():\n    return asyncio.get_running_loop().time()\n",
        [(3, "raw-clock-read")],
        id="raw-clock-read-loop-time-chain",
    ),
    pytest.param(
        "import asyncio\ndef f(cb):\n    loop = asyncio.get_event_loop()\n"
        "    loop.call_later(1, cb)\n    return loop.time()\n",
        [(4, "raw-clock-read"), (5, "raw-clock-read")],
        id="raw-clock-read-loop-alias",
    ),
    pytest.param(
        "def f():\n    return _service_time().monotonic()\n",
        [(2, "raw-clock-read")],
        id="raw-clock-read-service-time-seam",
    ),
    pytest.param(
        "from app.core.clock import REAL_CLOCK\ndef f():\n    return REAL_CLOCK.monotonic()\n",
        [(3, "raw-clock-read")],
        id="raw-clock-read-real-clock-singleton",
    ),
    pytest.param(
        "from app.core.clock import REAL_SCHEDULER as S\nasync def f(coro):\n"
        "    await S.sleep(1)\n    S.create_task(coro)\n    await S.wait_for(coro, 1)\n",
        [(3, "raw-sleep"), (4, "raw-task-spawn"), (5, "raw-timeout")],
        id="real-scheduler-singleton-by-member",
    ),
    pytest.param(
        "async def f(task):\n    await _needs_scheduler(task)\n",
        [(2, "missing-scheduler-kwarg")],
        id="missing-scheduler-kwarg",
    ),
    pytest.param(
        "async def f(task):\n    await _facade()._needs_scheduler(task)\n",
        [(2, "missing-scheduler-kwarg")],
        id="missing-scheduler-kwarg-via-facade-attribute",
    ),
    pytest.param(
        "class M:\n    async def f(self, task):\n        await self._needs_scheduler(task)\n",
        [(3, "missing-scheduler-kwarg")],
        id="missing-scheduler-kwarg-via-self",
    ),
    pytest.param(
        "async def f(task, scheduler):\n    await _needs_both(task, scheduler=scheduler)\n",
        [(2, "missing-scheduler-kwarg")],
        id="missing-clock-kwarg",
    ),
]


@pytest.mark.parametrize(("source", "expected"), _RAW_CASES)
def test_find_sites_reports_raw_timing_seams(tmp_path: Path, source: str, expected: list[tuple[int, str]]) -> None:
    checker = _load_checker_module()
    path = _write_module(tmp_path / "module.py", source)

    sites = checker.find_sites(path, _fixture_config(checker))

    assert [(site.line, site.rule) for site in sites] == expected


_ALLOWED_CASES = [
    pytest.param(
        "import asyncio\nasync def f():\n    await asyncio.sleep(0)\n    await asyncio.sleep(0.0)\n",
        id="sleep-zero-yield",
    ),
    pytest.param(
        "import asyncio\nasync def f(fs):\n    await asyncio.wait(fs)\n    await asyncio.wait(fs, timeout=None)\n",
        id="untimed-wait",
    ),
    pytest.param(
        "from app.core.utils.shared_future import wait_on_shared_future\n"
        "async def f(shared, t, s):\n"
        "    await wait_on_shared_future(shared)\n"
        "    await wait_on_shared_future(shared, timeout=None)\n"
        "    await wait_on_shared_future(shared, timeout=t, scheduler=s)\n",
        id="shared-future-with-scheduler-or-untimed",
    ),
    pytest.param(
        "from app.core.clock import clock_for, scheduler_for\n"
        "async def f(owner, coro, fut, delay):\n"
        "    scheduler_for(owner).create_task(coro)\n"
        "    await scheduler_for(owner).sleep(delay)\n"
        "    await scheduler_for(owner).wait_for(fut, timeout=1)\n"
        "    with scheduler_for(owner).fail_after(1):\n"
        "        pass\n"
        "    return clock_for(owner).monotonic()\n",
        id="injected-seams",
    ),
    pytest.param(
        "import asyncio\nimport anyio\nasync def f(task, fs):\n"
        "    asyncio.shield(task)\n    await asyncio.gather(*fs)\n    asyncio.get_running_loop().create_future()\n"
        "    with anyio.CancelScope(shield=True):\n        pass\n"
        "    await asyncio.to_thread(print)\n    asyncio.current_task()\n    asyncio.Event()\n",
        id="not-timing-seams",
    ),
    pytest.param(
        "from app.core.utils import utcnow\nfrom datetime import datetime, timezone\n"
        "def f():\n    return utcnow(), datetime.now(timezone.utc)\n",
        id="wall-clock-datetime-helpers",
    ),
    pytest.param(
        "async def f(asyncio, time, delay):\n    await asyncio.sleep(delay)\n    return time.monotonic()\n",
        id="parameter-shadowing-suppresses",
    ),
    pytest.param(
        "async def f(task, scheduler, clock):\n"
        "    await _needs_scheduler(task, scheduler=scheduler)\n"
        "    await _needs_both(task, scheduler=scheduler, clock=clock)\n"
        "    await _facade()._needs_scheduler(task, scheduler=scheduler)\n",
        id="required-kwargs-passed",
    ),
    pytest.param(
        "async def f(task, **kwargs):\n    await _needs_both(task, **kwargs)\n",
        id="kwargs-splat-satisfies-required-kwargs",
    ),
    pytest.param(
        "def _needs_scheduler(task, *, scheduler):\n    return task\n",
        id="definition-is-not-a-call",
    ),
    pytest.param(
        "import asyncio\nasync def f(loop, coro):\n    loop.create_task(coro)\n    return loop.time()\n",
        id="unknown-loop-object-is-not-resolved",
    ),
]


@pytest.mark.parametrize("source", _ALLOWED_CASES)
def test_find_sites_allows_sanctioned_shapes(tmp_path: Path, source: str) -> None:
    checker = _load_checker_module()
    path = _write_module(tmp_path / "module.py", source)

    assert checker.find_sites(path, _fixture_config(checker)) == []


def test_find_sites_raises_named_parse_failure(tmp_path: Path) -> None:
    checker = _load_checker_module()
    path = _write_module(tmp_path / "broken.py", "def f(:\n")

    with pytest.raises(AssertionError, match=r"broken\.py could not be parsed: "):
        checker.find_sites(path)


def test_main_clean_fixture_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker_module()
    _configure_fixture(checker, tmp_path, monkeypatch)

    assert checker.main([]) == 0

    captured = capsys.readouterr()
    assert captured.out == "proxy timing seam checks passed\n"
    assert captured.err == ""


def test_main_reports_every_site_of_an_over_budget_module_in_stable_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker_module()
    proxy_dir = _configure_fixture(checker, tmp_path, monkeypatch)
    _write_module(
        proxy_dir / "_service" / "lifecycle.py",
        """
        import asyncio
        import time

        async def turn(coro, fut, delay):
            asyncio.create_task(coro)
            await asyncio.sleep(delay)
            started = time.monotonic()
            await _needs_scheduler(fut)
            return time.monotonic() - started
        """,
    )
    _write_spec(
        checker.PROXY_ARCHITECTURE_SPEC_PATH,
        _SPEC_BLOCK.replace(
            "[allowances.clock]\n", '[allowances.clock]\n"app/modules/proxy/_service/lifecycle.py" = 1\n'
        ),
    )

    assert checker.main([]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    prefix = "proxy timing seam check failed: app/modules/proxy/_service/lifecycle.py"
    assert captured.err.splitlines() == [
        f"{prefix}:5: raw-task-spawn asyncio.create_task(...); use scheduler_for(owner).create_task(...)",
        f"{prefix}:6: raw-sleep asyncio.sleep(...); "
        "use scheduler_for(owner).sleep(...); only a literal sleep(0) yield point stays raw",
        f"{prefix}:8: missing-scheduler-kwarg _needs_scheduler(...) without scheduler=; "
        "pass the collaborator explicitly (REAL_SCHEDULER/REAL_CLOCK when no owner is in scope)",
        f"{prefix} has 3 raw timing sites; allowance is 0",
        f"{prefix}:7: raw-clock-read time.monotonic(...); "
        "use clock_for(owner).monotonic()/time() or thread the caller's now=",
        f"{prefix}:9: raw-clock-read time.monotonic(...); "
        "use clock_for(owner).monotonic()/time() or thread the caller's now=",
        f"{prefix} has 2 raw clock sites; allowance is 1",
    ]


def test_main_reports_allowance_for_missing_module_and_keeps_scanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker_module()
    proxy_dir = _configure_fixture(checker, tmp_path, monkeypatch)
    _write_module(proxy_dir / "fresh.py", "import time\n\ndef f():\n    return time.time()\n")
    _write_spec(checker.PROXY_ARCHITECTURE_SPEC_PATH, _SPEC_BLOCK + '"app/modules/proxy/gone.py" = 2\n')

    assert checker.main([]) == 1

    captured = capsys.readouterr()
    assert captured.err.splitlines() == [
        "proxy timing seam check failed: app/modules/proxy/fresh.py:4: raw-clock-read time.time(...); "
        "use clock_for(owner).monotonic()/time() or thread the caller's now=",
        "proxy timing seam check failed: app/modules/proxy/fresh.py has 1 raw clock sites; allowance is 0",
        "proxy timing seam check failed: openspec/specs/proxy-architecture/spec.md timing seam allowances.clock "
        "lists app/modules/proxy/gone.py, which is not a scanned module",
    ]


@pytest.mark.parametrize(
    ("transform", "expected_detail"),
    [
        pytest.param(
            lambda text: text.replace("<!-- proxy-timing-seams:start -->", ""),
            "must contain exactly one marked timing seam block",
            id="missing-marker",
        ),
        pytest.param(
            lambda text: text + "\n<!-- proxy-timing-seams:start -->\n",
            "must contain exactly one marked timing seam block",
            id="duplicate-marker",
        ),
        pytest.param(
            lambda text: text.replace("```toml", "```text"),
            "timing seam block must contain one TOML fence",
            id="wrong-fence",
        ),
        pytest.param(
            lambda text: text.replace('_needs_scheduler = "scheduler"', "_needs_scheduler ="),
            "timing seam block contains invalid TOML",
            id="malformed-toml",
        ),
        pytest.param(
            lambda text: text.replace("[scheduler_kwarg_required]", "extra = 1\n[scheduler_kwarg_required]"),
            "timing seam block has unknown keys: extra",
            id="unknown-key",
        ),
        pytest.param(
            lambda text: text.replace("[allowances.clock]", "[allowances.spawn]\n[allowances.clock]"),
            "timing seam allowances has unknown tables: spawn",
            id="unknown-allowance-table",
        ),
        pytest.param(
            lambda text: text.replace(
                '[allowances.clock]\n"app/modules/proxy/allowed.py" = 1',
                '[allowances.clock]\n"app/modules/proxy/allowed.py" = 0',
            ),
            "timing seam allowance allowances.clock.'app/modules/proxy/allowed.py' must be a positive integer",
            id="non-positive-allowance",
        ),
        pytest.param(
            lambda text: text.replace(
                '[allowances.timing]\n"app/modules/proxy/allowed.py" = 1',
                '[allowances.timing]\n"app/modules/proxy/allowed.py" = "1"',
            ),
            "timing seam allowance allowances.timing.'app/modules/proxy/allowed.py' must be a positive integer",
            id="non-integer-allowance",
        ),
        pytest.param(
            lambda text: text.replace('_needs_scheduler = "scheduler"', "_needs_scheduler = []"),
            "timing seam scheduler_kwarg_required._needs_scheduler must be a keyword name or a list of distinct names",
            id="empty-required-keyword-list",
        ),
        pytest.param(
            lambda text: text.replace(
                '_needs_both = ["scheduler", "clock"]', '_needs_both = ["scheduler", "scheduler"]'
            ),
            "timing seam scheduler_kwarg_required._needs_both must be a keyword name or a list of distinct names",
            id="duplicate-required-keyword",
        ),
    ],
)
def test_main_rejects_invalid_timing_seam_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    transform: Callable[[str], str],
    expected_detail: str,
) -> None:
    checker = _load_checker_module()
    _configure_fixture(checker, tmp_path, monkeypatch)
    spec_path = checker.PROXY_ARCHITECTURE_SPEC_PATH
    spec_path.write_text(transform(spec_path.read_text(encoding="utf-8")), encoding="utf-8")

    assert checker.main([]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        f"proxy timing seam check failed: openspec/specs/proxy-architecture/spec.md {expected_detail}",
    ]


def test_main_reports_definition_failure_and_parse_failure_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker_module()
    proxy_dir = _configure_fixture(checker, tmp_path, monkeypatch)
    _write_module(proxy_dir / "broken.py", "def f(:\n")
    checker.PROXY_ARCHITECTURE_SPEC_PATH.write_bytes(b"\xff")

    assert checker.main([]) == 1

    captured = capsys.readouterr()
    failures = captured.err.splitlines()
    assert failures[0] == (
        "proxy timing seam check failed: openspec/specs/proxy-architecture/spec.md "
        "timing seam definition is not valid UTF-8"
    )
    assert len(failures) == 2
    assert failures[1].startswith("proxy timing seam check failed: app/modules/proxy/broken.py could not be parsed:")


def test_report_and_explain_describe_the_scanned_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checker = _load_checker_module()
    _configure_fixture(checker, tmp_path, monkeypatch)

    assert checker.main(["--explain"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "app/modules/proxy/allowed.py:5: raw-task-spawn asyncio.create_task(...)",
        "app/modules/proxy/allowed.py:6: raw-clock-read time.monotonic(...)",
    ]

    assert checker.main(["--report"]) == 0
    assert tomllib.loads(capsys.readouterr().out) == {
        "scheduler_kwarg_required": {"_needs_scheduler": "scheduler", "_needs_both": ["scheduler", "clock"]},
        "allowances": {
            "timing": {"app/modules/proxy/allowed.py": 1},
            "clock": {"app/modules/proxy/allowed.py": 1},
        },
    }


def test_repository_timing_seams_pass(capsys: pytest.CaptureFixture[str]) -> None:
    checker = _load_checker_module()

    assert checker.main([]) == 0

    captured = capsys.readouterr()
    assert captured.out == "proxy timing seam checks passed\n"
    assert captured.err == ""


def test_repository_allowances_are_exact() -> None:
    """Headroom cannot accrue silently: removing a raw site must lower its allowance in the same diff."""

    checker = _load_checker_module()
    config = checker.load_config()
    report = checker.repository_report(checker.scanned_paths(), config)
    counts = {module_path: checker._counts(sites) for module_path, sites in report.items()}

    assert {path: counts[path].timing for path in counts if counts[path].timing} == config.timing_allowances
    assert {path: counts[path].clock for path in counts if counts[path].clock} == config.clock_allowances


def test_repository_report_reproduces_committed_block() -> None:
    checker = _load_checker_module()
    config = checker.load_config()
    spec_text = checker.PROXY_ARCHITECTURE_SPEC_PATH.read_text(encoding="utf-8")
    _prefix, _start, remainder = spec_text.partition("<!-- proxy-timing-seams:start -->")
    block, _end, _suffix = remainder.partition("<!-- proxy-timing-seams:end -->")
    committed = tomllib.loads("\n".join(block.strip().splitlines()[1:-1]))

    rendered = checker.render_report(checker.repository_report(checker.scanned_paths(), config), config)

    assert tomllib.loads(rendered) == committed
