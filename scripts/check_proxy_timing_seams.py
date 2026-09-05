#!/usr/bin/env python3
"""Reject raw timing, task-spawn and clock sites in the proxy turn lifecycle.

The deterministic simulation harness (``tests/simulation``) can only own the
tasks and timers a proxy turn creates while every such site goes through the
``Scheduler``/``Clock`` seams in ``app/core/clock.py``. A single raw
``asyncio.sleep`` or ``time.monotonic()`` added to a lifecycle module silently
puts that path back on the wall clock without failing any test, so this gate
counts the raw sites per module and compares them with an OpenSpec-owned
allowance table. Unlisted modules have an allowance of zero.

Rules (the id is printed verbatim and used as the test id):

* ``raw-sleep``: ``asyncio.sleep``/``anyio.sleep`` with a non-zero delay.
  A literal ``sleep(0)`` is a yield point, not a timer, and stays raw.
* ``raw-timeout``: ``asyncio.wait_for``, ``asyncio.wait(..., timeout=)``,
  ``asyncio.timeout``/``timeout_at``, ``anyio.fail_after``/``move_on_after``
  and a timed ``wait_on_shared_future`` without ``scheduler=``.
* ``raw-task-spawn``: ``asyncio.create_task``, ``asyncio.ensure_future``,
  ``asyncio.TaskGroup`` and ``<loop>.create_task`` on an event loop obtained
  from ``asyncio.get_running_loop()``/``get_event_loop()``.
* ``raw-clock-read``: ``time.monotonic()``/``time()``/``perf_counter()``,
  ``<loop>.time()``/``call_later``/``call_at``, the legacy
  ``_service_time().<x>()`` seam and direct ``REAL_CLOCK.<x>()`` calls
  (including the ``time.time() if now is None else now`` default idiom, so a
  new hidden wall-clock default is visible in the table).
* ``missing-scheduler-kwarg``: a call to a function listed in
  ``[scheduler_kwarg_required]`` that omits one of its required keywords.
  The callee is matched by its terminal name, so ``_facade()._x(...)``,
  ``self._x(...)`` and bare ``_x(...)`` are all covered. Passing an explicit
  ``REAL_SCHEDULER``/``REAL_CLOCK`` is allowed: it is visible and reviewable.
  A ``**kwargs`` splat is taken as satisfying the requirement.

``REAL_SCHEDULER.<x>()`` bypasses injection too and is classified by member
(``sleep`` -> raw-sleep, ``create_task`` -> raw-task-spawn, otherwise
raw-timeout). Not matched on purpose because they are not timing seams:
``asyncio.shield``, ``asyncio.gather``, an untimed ``asyncio.wait``,
``asyncio.Event``/``Lock``/``Semaphore``/``Queue``/``Future``,
``loop.create_future()``, ``anyio.CancelScope(shield=True)``,
``asyncio.to_thread``, ``asyncio.current_task``/``all_tasks``, ``utcnow()``
and ``datetime.now()``.

Alias discovery deliberately covers every import in the file, including
nested ones, and the canonical ``asyncio``/``anyio``/``time`` names are always
recognised: a guard against silent rot must prefer a visible false positive
(fixable by renaming) over an evasion through a function-local import. A
function parameter that shadows a recognised name still suppresses the match.

The only escape hatch is the marked TOML block in
``openspec/specs/proxy-architecture/spec.md``; ``--report`` prints the block
that matches the current tree and ``--explain`` lists every site with its
rule so a reviewer can tell a missed injection from an accepted residual.
"""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROXY_DIR = ROOT / "app" / "modules" / "proxy"
SHARED_FUTURE_PATH = ROOT / "app" / "core" / "utils" / "shared_future.py"
PROXY_ARCHITECTURE_SPEC_PATH = ROOT / "openspec" / "specs" / "proxy-architecture" / "spec.md"

_BLOCK_START = "<!-- proxy-timing-seams:start -->"
_BLOCK_END = "<!-- proxy-timing-seams:end -->"
_FAILURE_PREFIX = "proxy timing seam check failed"

RULE_RAW_SLEEP = "raw-sleep"
RULE_RAW_TIMEOUT = "raw-timeout"
RULE_RAW_TASK_SPAWN = "raw-task-spawn"
RULE_RAW_CLOCK_READ = "raw-clock-read"
RULE_MISSING_KWARG = "missing-scheduler-kwarg"

TIMING_RULES = frozenset({RULE_RAW_SLEEP, RULE_RAW_TIMEOUT, RULE_RAW_TASK_SPAWN, RULE_MISSING_KWARG})
CLOCK_RULES = frozenset({RULE_RAW_CLOCK_READ})

_SUGGESTIONS = {
    RULE_RAW_SLEEP: "use scheduler_for(owner).sleep(...); only a literal sleep(0) yield point stays raw",
    RULE_RAW_TIMEOUT: "use scheduler_for(owner).wait_for/wait/fail_after or pass scheduler= to wait_on_shared_future",
    RULE_RAW_TASK_SPAWN: "use scheduler_for(owner).create_task(...)",
    RULE_RAW_CLOCK_READ: "use clock_for(owner).monotonic()/time() or thread the caller's now=",
    RULE_MISSING_KWARG: "pass the collaborator explicitly (REAL_SCHEDULER/REAL_CLOCK when no owner is in scope)",
}

_TRACKED_MODULES = ("asyncio", "anyio", "time")
_TRACKED_NAMES = ("REAL_CLOCK", "REAL_SCHEDULER", "_service_time", "wait_on_shared_future")
_LOOP_FACTORIES = frozenset({("asyncio", "get_running_loop"), ("asyncio", "get_event_loop")})
_TIMEOUT_CALLS = frozenset(
    {
        ("asyncio", "wait_for"),
        ("asyncio", "timeout"),
        ("asyncio", "timeout_at"),
        ("anyio", "fail_after"),
        ("anyio", "move_on_after"),
    }
)
_SPAWN_CALLS = frozenset({("asyncio", "create_task"), ("asyncio", "ensure_future"), ("asyncio", "TaskGroup")})
_CLOCK_MEMBERS = frozenset({"monotonic", "time", "perf_counter"})
_LOOP_CLOCK_MEMBERS = frozenset({"time", "call_later", "call_at"})
_LOOP = "<loop>"
_SERVICE_TIME = "<_service_time()>"

_ScopeNode = ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda


@dataclass(frozen=True, slots=True)
class Site:
    path: Path
    line: int
    rule: str
    spelling: str
    suggestion: str


@dataclass(frozen=True, slots=True)
class Counts:
    timing: int = 0
    clock: int = 0


@dataclass(frozen=True, slots=True)
class Config:
    scheduler_kwarg_required: dict[str, tuple[str, ...]] = field(default_factory=dict)
    timing_allowances: dict[str, int] = field(default_factory=dict)
    clock_allowances: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Aliases:
    modules: dict[str, str]
    members: dict[str, tuple[str, str]]
    names: dict[str, str]


def _relative_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _qualified_name(node: ast.expr) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return tuple(reversed(parts))
    return ()


def _import_aliases(module: ast.Module) -> _Aliases:
    modules = {name: name for name in _TRACKED_MODULES}
    members: dict[str, tuple[str, str]] = {}
    names = {name: name for name in _TRACKED_NAMES}
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name in _TRACKED_MODULES:
                    modules[imported.asname or imported.name] = imported.name
        elif isinstance(node, ast.ImportFrom):
            source = node.module or ""
            for imported in node.names:
                alias = imported.asname or imported.name
                if source in _TRACKED_MODULES:
                    members[alias] = (source, imported.name)
                elif imported.name in _TRACKED_NAMES:
                    names[alias] = imported.name
    return _Aliases(modules=modules, members=members, names=names)


def _shadowed_by_parameter(node: ast.AST, name: str, parents: dict[ast.AST, ast.AST]) -> bool:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            arguments = current.args
            parameters = (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
            if any(parameter.arg == name for parameter in parameters):
                return True
            if arguments.vararg is not None and arguments.vararg.arg == name:
                return True
            if arguments.kwarg is not None and arguments.kwarg.arg == name:
                return True
        current = parents.get(current)
    return False


def _enclosing_scopes(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> Iterable[_ScopeNode]:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, _ScopeNode):
            yield current
        current = parents.get(current)


def _is_zero_literal(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and type(node.value) in (int, float)  # ``False`` is not a zero delay
        and node.value == 0
    )


def _keyword(call: ast.Call, name: str) -> ast.keyword | None:
    return next((keyword for keyword in call.keywords if keyword.arg == name), None)


def _is_none_constant(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


class _Scanner:
    def __init__(self, path: Path, module: ast.Module, config: Config) -> None:
        self._path = path
        self._config = config
        self._aliases = _import_aliases(module)
        self._parents = {child: parent for parent in ast.walk(module) for child in ast.iter_child_nodes(parent)}
        # Loop aliases are resolved through ``_resolve_module_call``, which
        # consults this map, so it must exist (empty) before it is filled.
        self._loop_names: dict[_ScopeNode, set[str]] = {}
        self._loop_names = self._collect_loop_names(module)
        self.sites: list[Site] = []

    def _collect_loop_names(self, module: ast.Module) -> dict[_ScopeNode, set[str]]:
        names: dict[_ScopeNode, set[str]] = {}
        for node in ast.walk(module):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value = (node.target,), node.value
            else:
                continue
            if not isinstance(value, ast.Call) or self._resolve_module_call(value.func) not in _LOOP_FACTORIES:
                continue
            scope = next(iter(self._enclosing_scopes(node)), None)
            if scope is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    names.setdefault(scope, set()).add(target.id)
        return names

    def _enclosing_scopes(self, node: ast.AST) -> Iterable[_ScopeNode]:
        return _enclosing_scopes(node, self._parents)

    def _is_loop_name(self, node: ast.Name) -> bool:
        return any(node.id in self._loop_names.get(scope, ()) for scope in self._enclosing_scopes(node))

    def _resolve_module_call(self, func: ast.expr) -> tuple[str, ...]:
        """Resolve ``func`` to a canonical ``(module_or_name, member...)`` tuple."""

        if isinstance(func, ast.Name):
            if _shadowed_by_parameter(func, func.id, self._parents):
                return ()
            member = self._aliases.members.get(func.id)
            if member is not None:
                return member
            name = self._aliases.names.get(func.id)
            return (name,) if name is not None else ()
        if not isinstance(func, ast.Attribute):
            return ()
        if isinstance(func.value, ast.Call):
            inner = self._resolve_module_call(func.value.func)
            if inner in _LOOP_FACTORIES:
                return (_LOOP, func.attr)
            if inner == ("_service_time",):
                return (_SERVICE_TIME, func.attr)
            return ()
        qualified = _qualified_name(func)
        if len(qualified) < 2:
            return ()
        root = qualified[0]
        root_node = func
        while isinstance(root_node, ast.Attribute):
            root_node = root_node.value
        if _shadowed_by_parameter(func, root, self._parents):
            return ()
        module = self._aliases.modules.get(root)
        if module is not None:
            return (module, *qualified[1:])
        name = self._aliases.names.get(root)
        if name is not None:
            return (name, *qualified[1:])
        if isinstance(root_node, ast.Name) and self._is_loop_name(root_node):
            return (_LOOP, *qualified[1:])
        return ()

    def _record(self, call: ast.Call, rule: str, spelling: str | None = None) -> None:
        self.sites.append(
            Site(
                path=self._path,
                line=call.lineno,
                rule=rule,
                spelling=spelling or f"{ast.unparse(call.func)}(...)",
                suggestion=_SUGGESTIONS[rule],
            )
        )

    def _classify_real_scheduler(self, member: str) -> str:
        if member == "sleep":
            return RULE_RAW_SLEEP
        if member == "create_task":
            return RULE_RAW_TASK_SPAWN
        return RULE_RAW_TIMEOUT

    def visit_call(self, call: ast.Call) -> None:
        resolved = self._resolve_module_call(call.func)
        if resolved in (("asyncio", "sleep"), ("anyio", "sleep")):
            delay = call.args[0] if call.args else None
            if delay is None:
                keyword = _keyword(call, "delay")
                delay = keyword.value if keyword is not None else None
            if delay is None or not _is_zero_literal(delay):
                self._record(call, RULE_RAW_SLEEP)
        elif resolved in _TIMEOUT_CALLS:
            self._record(call, RULE_RAW_TIMEOUT)
        elif resolved == ("asyncio", "wait"):
            timeout = _keyword(call, "timeout")
            if timeout is not None and not _is_none_constant(timeout.value):
                self._record(call, RULE_RAW_TIMEOUT, f"{ast.unparse(call.func)}(..., timeout=...)")
        elif resolved == ("wait_on_shared_future",):
            timeout = _keyword(call, "timeout")
            if timeout is not None and not _is_none_constant(timeout.value) and _keyword(call, "scheduler") is None:
                self._record(call, RULE_RAW_TIMEOUT, f"{ast.unparse(call.func)}(..., timeout=...) without scheduler=")
        elif resolved in _SPAWN_CALLS or resolved == (_LOOP, "create_task"):
            self._record(call, RULE_RAW_TASK_SPAWN)
        elif len(resolved) == 2 and resolved[0] == "time" and resolved[1] in _CLOCK_MEMBERS:
            self._record(call, RULE_RAW_CLOCK_READ)
        elif len(resolved) == 2 and resolved[0] == _LOOP and resolved[1] in _LOOP_CLOCK_MEMBERS:
            self._record(call, RULE_RAW_CLOCK_READ)
        elif len(resolved) == 2 and resolved[0] in (_SERVICE_TIME, "REAL_CLOCK"):
            self._record(call, RULE_RAW_CLOCK_READ)
        elif len(resolved) == 2 and resolved[0] == "REAL_SCHEDULER":
            self._record(call, self._classify_real_scheduler(resolved[1]))
        self._check_required_keywords(call)

    def _check_required_keywords(self, call: ast.Call) -> None:
        if isinstance(call.func, ast.Attribute):
            callee = call.func.attr
        elif isinstance(call.func, ast.Name):
            callee = call.func.id
            if _shadowed_by_parameter(call.func, callee, self._parents):
                return
        else:
            return
        required = self._config.scheduler_kwarg_required.get(callee)
        if required is None:
            return
        passed = {keyword.arg for keyword in call.keywords}
        if None in passed:  # ``**kwargs`` splat: the keywords cannot be inspected statically
            return
        missing = [keyword for keyword in required if keyword not in passed]
        if missing:
            spelling = f"{ast.unparse(call.func)}(...) without {', '.join(f'{keyword}=' for keyword in missing)}"
            self._record(call, RULE_MISSING_KWARG, spelling)


def parse_module(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        location = f" at line {exc.lineno}" if exc.lineno is not None else ""
        raise AssertionError(f"{_relative_path(path)} could not be parsed: {exc.msg}{location}") from None


def find_sites(path: Path, config: Config | None = None) -> list[Site]:
    """Return every raw timing/clock site in ``path`` in source order."""

    module = parse_module(path)
    scanner = _Scanner(path, module, config if config is not None else Config())
    for node in ast.walk(module):
        if isinstance(node, ast.Call):
            scanner.visit_call(node)
    return sorted(scanner.sites, key=lambda site: (site.line, site.rule, site.spelling))


def _config_failure(path: Path, detail: str) -> AssertionError:
    return AssertionError(f"{_relative_path(path)} {detail}")


def _required_keywords(path: Path, name: str, value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise _config_failure(
            path, f"timing seam scheduler_kwarg_required.{name} must be a keyword name or a list of distinct names"
        )
    return tuple(value)


def _allowances(path: Path, table_name: str, value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        raise _config_failure(path, f"timing seam allowances.{table_name} must be a table of module allowances")
    allowances: dict[str, int] = {}
    for module_path, allowance in value.items():
        if type(allowance) is not int or allowance <= 0:
            raise _config_failure(
                path, f"timing seam allowance allowances.{table_name}.{module_path!r} must be a positive integer"
            )
        allowances[module_path] = allowance
    return allowances


def load_config(spec_path: Path | None = None) -> Config:
    path = PROXY_ARCHITECTURE_SPEC_PATH if spec_path is None else spec_path
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError:
        raise _config_failure(path, "timing seam definition is not valid UTF-8") from None
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        raise _config_failure(path, f"timing seam definition could not be read: {detail}") from None

    if text.count(_BLOCK_START) != 1 or text.count(_BLOCK_END) != 1:
        raise _config_failure(path, "must contain exactly one marked timing seam block")
    _prefix, _start, remainder = text.partition(_BLOCK_START)
    block, end, _suffix = remainder.partition(_BLOCK_END)
    if not end:
        raise _config_failure(path, "must contain exactly one marked timing seam block")

    lines = block.strip().splitlines()
    if len(lines) < 3 or lines[0].strip() != "```toml" or lines[-1].strip() != "```":
        raise _config_failure(path, "timing seam block must contain one TOML fence")
    try:
        values = tomllib.loads("\n".join(lines[1:-1]))
    except tomllib.TOMLDecodeError:
        raise _config_failure(path, "timing seam block contains invalid TOML") from None

    unknown_keys = sorted(set(values) - {"scheduler_kwarg_required", "allowances"})
    if unknown_keys:
        raise _config_failure(path, "timing seam block has unknown keys: " + ", ".join(unknown_keys))

    kwarg_table = values.get("scheduler_kwarg_required", {})
    if not isinstance(kwarg_table, dict):
        raise _config_failure(path, "timing seam scheduler_kwarg_required must be a table")
    required = {name: _required_keywords(path, name, value) for name, value in kwarg_table.items()}

    allowance_tables = values.get("allowances", {})
    if not isinstance(allowance_tables, dict):
        raise _config_failure(path, "timing seam allowances must be a table")
    unknown_tables = sorted(set(allowance_tables) - {"timing", "clock"})
    if unknown_tables:
        raise _config_failure(path, "timing seam allowances has unknown tables: " + ", ".join(unknown_tables))
    return Config(
        scheduler_kwarg_required=required,
        timing_allowances=_allowances(path, "timing", allowance_tables.get("timing", {})),
        clock_allowances=_allowances(path, "clock", allowance_tables.get("clock", {})),
    )


def scanned_paths(proxy_dir: Path | None = None, shared_future_path: Path | None = None) -> list[Path]:
    proxy = PROXY_DIR if proxy_dir is None else proxy_dir
    shared = SHARED_FUTURE_PATH if shared_future_path is None else shared_future_path
    return [*sorted(proxy.rglob("*.py")), shared]


def _counts(sites: Iterable[Site]) -> Counts:
    timing = clock = 0
    for site in sites:
        if site.rule in TIMING_RULES:
            timing += 1
        elif site.rule in CLOCK_RULES:
            clock += 1
    return Counts(timing=timing, clock=clock)


def repository_report(paths: Sequence[Path], config: Config) -> dict[str, list[Site]]:
    """Return every site per repo-relative module path (modules without sites omitted)."""

    report: dict[str, list[Site]] = {}
    for path in paths:
        sites = find_sites(path, config)
        if sites:
            report[_relative_path(path)] = sites
    return report


def _format_site(site: Site) -> str:
    return f"{_relative_path(site.path)}:{site.line}: {site.rule} {site.spelling}; {site.suggestion}"


def collect_failures(spec_path: Path | None = None, paths: Sequence[Path] | None = None) -> list[str]:
    spec_path = PROXY_ARCHITECTURE_SPEC_PATH if spec_path is None else spec_path
    failures: list[str] = []
    scanned = scanned_paths() if paths is None else list(paths)
    try:
        config: Config | None = load_config(spec_path)
    except AssertionError as exc:
        failures.append(str(exc))
        config = None

    scanned_relative: set[str] = set()
    for path in scanned:
        relative = _relative_path(path)
        scanned_relative.add(relative)
        try:
            sites = find_sites(path, config if config is not None else Config())
        except AssertionError as exc:
            failures.append(str(exc))
            continue
        if config is None:
            # Allowances cannot be evaluated without a valid definition; parse
            # failures above are the only independently evaluable findings.
            continue
        counts = _counts(sites)
        for category, rules, allowance in (
            ("timing", TIMING_RULES, config.timing_allowances.get(relative, 0)),
            ("clock", CLOCK_RULES, config.clock_allowances.get(relative, 0)),
        ):
            count = getattr(counts, category)
            if count <= allowance:
                continue
            failures.extend(_format_site(site) for site in sites if site.rule in rules)
            failures.append(f"{relative} has {count} raw {category} sites; allowance is {allowance}")
    if config is not None:
        for table_name, table in (("timing", config.timing_allowances), ("clock", config.clock_allowances)):
            for module_path in sorted(set(table) - scanned_relative):
                failures.append(
                    f"{_relative_path(spec_path)} timing seam allowances.{table_name} lists {module_path}, "
                    "which is not a scanned module"
                )
    return failures


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_report(report: dict[str, list[Site]], config: Config) -> str:
    """Render the TOML block matching ``report`` (allowances) and ``config`` (required keywords)."""

    lines = ["[scheduler_kwarg_required]"]
    for name in sorted(config.scheduler_kwarg_required):
        keywords = config.scheduler_kwarg_required[name]
        rendered = (
            _toml_string(keywords[0]) if len(keywords) == 1 else "[" + ", ".join(map(_toml_string, keywords)) + "]"
        )
        lines.append(f"{name} = {rendered}")
    for category, comment in (
        ("timing", "raw-sleep + raw-timeout + raw-task-spawn + missing-scheduler-kwarg; unlisted modules = 0"),
        ("clock", "raw-clock-read; unlisted modules = 0"),
    ):
        lines.extend(("", f"[allowances.{category}]  # {comment}"))
        for module_path in sorted(report):
            count = getattr(_counts(report[module_path]), category)
            if count:
                lines.append(f"{_toml_string(module_path)} = {count}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce scheduler/clock seams in the proxy turn lifecycle.")
    parser.add_argument("--report", action="store_true", help="print the allowance TOML matching the current tree")
    parser.add_argument("--explain", action="store_true", help="list every raw site with its rule id")
    arguments = parser.parse_args(argv)

    if arguments.report or arguments.explain:
        try:
            config = load_config()
        except AssertionError as exc:
            print(f"{_FAILURE_PREFIX}: {exc}", file=sys.stderr)
            return 1
        report = repository_report(scanned_paths(), config)
        if arguments.explain:
            for module_path in sorted(report):
                for site in report[module_path]:
                    print(f"{module_path}:{site.line}: {site.rule} {site.spelling}")
        if arguments.report:
            print(render_report(report, config), end="")
        return 0

    failures = collect_failures()
    if failures:
        for failure in failures:
            print(f"{_FAILURE_PREFIX}: {failure}", file=sys.stderr)
        return 1
    print("proxy timing seam checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
