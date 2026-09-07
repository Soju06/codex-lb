#!/usr/bin/env python3
"""Reject cancellation-unsafe waits on owned asyncio tasks.

Two shapes are rejected:

1. A loop that catches caller cancellation and retries an ``asyncio.shield()``
   wait (the 2026-08-30 event-loop livelock).
2. A bare ``await`` of a task that may defer cancellation -- one whose
   coroutine drives an async iterator (``anext``/``__anext__``/``async for``)
   or calls a cancellation-deferring helper. A level-cancelled anyio scope
   re-cancels its host task every loop iteration; ``Task.cancel()`` cascades
   down the ``_fut_waiter`` chain into a directly awaited task, so the
   awaited task's deferring wait is re-entered once per iteration for as long
   as its cleanup takes (the 2026-09-07 busy spin). Await such tasks through
   ``wait_on_shared_future`` or ``_await_task_deferring_cancellation`` so the
   proxy future, not the owned task, absorbs the repeated cancels.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    line: int
    reason: str = "cancellation-catching loop retries asyncio.shield; use wait_on_shared_future"


_DEFERRING_HELPER_NAMES = frozenset(
    {
        "_await_task_deferring_cancellation",
        "_await_cleanup_deferring_cancellation",
        "_await_result_deferring_cancellation",
    }
)
_TASK_FACTORY_ATTRS = frozenset({"create_task", "ensure_future"})
_PROBE_TASK_FACTORIES = frozenset({"_create_first_stream_probe_task"})
_DIRECT_AWAIT_REASON = (
    "direct await of a task that may defer cancellation; "
    "await it through wait_on_shared_future or _await_task_deferring_cancellation"
)


@dataclass(frozen=True, slots=True)
class _ImportAliases:
    asyncio_modules: frozenset[str]
    asyncio_shields: frozenset[str]
    asyncio_waits: frozenset[str]
    cancellation_exceptions: frozenset[str]


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


def _import_aliases(module: ast.Module) -> _ImportAliases:
    asyncio_modules: set[str] = set()
    asyncio_shields: set[str] = set()
    asyncio_waits: set[str] = set()
    cancellation_exceptions = {"BaseException"}
    # Application imports are module-level by convention. Restrict discovery to
    # that scope so an unrelated nested import cannot redefine aliases for the
    # entire file.
    for node in module.body:
        if isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name == "asyncio":
                    asyncio_modules.add(imported.asname or imported.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "asyncio":
            for imported in node.names:
                if imported.name == "shield":
                    asyncio_shields.add(imported.asname or imported.name)
                elif imported.name == "wait":
                    asyncio_waits.add(imported.asname or imported.name)
                elif imported.name == "CancelledError":
                    cancellation_exceptions.add(imported.asname or imported.name)
    return _ImportAliases(
        asyncio_modules=frozenset(asyncio_modules),
        asyncio_shields=frozenset(asyncio_shields),
        asyncio_waits=frozenset(asyncio_waits),
        cancellation_exceptions=frozenset(cancellation_exceptions),
    )


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


def _is_asyncio_shield(
    call: ast.Call,
    aliases: _ImportAliases,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    name = _qualified_name(call.func)
    return (
        len(name) == 2
        and name[0] in aliases.asyncio_modules
        and name[1] == "shield"
        and not _shadowed_by_parameter(call, name[0], parents)
    ) or (len(name) == 1 and name[0] in aliases.asyncio_shields and not _shadowed_by_parameter(call, name[0], parents))


def _catches_cancellation(
    handler: ast.ExceptHandler,
    aliases: _ImportAliases,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    if handler.type is None:
        return True
    candidates = handler.type.elts if isinstance(handler.type, ast.Tuple) else (handler.type,)
    for candidate in candidates:
        name = _qualified_name(candidate)
        if (
            len(name) == 2
            and name[0] in aliases.asyncio_modules
            and name[1] == "CancelledError"
            and not _shadowed_by_parameter(candidate, name[0], parents)
        ):
            return True
        if (
            len(name) == 1
            and name[0] in aliases.cancellation_exceptions
            and not _shadowed_by_parameter(candidate, name[0], parents)
        ):
            return True
    return False


def _sequence_outcome(statements: list[ast.stmt]) -> tuple[bool, bool]:
    """Return possible ``(fallthrough, retry)`` outcomes."""

    can_fall_through = True
    can_retry = False
    for statement in statements:
        if not can_fall_through:
            break
        statement_falls_through, statement_retries = _statement_outcome(statement)
        can_retry = can_retry or statement_retries
        can_fall_through = statement_falls_through
    return can_fall_through, can_retry


def _statement_outcome(statement: ast.stmt) -> tuple[bool, bool]:
    if isinstance(statement, ast.Continue | ast.Break):
        # Treat break conservatively: an enclosing loop can re-enter the shield.
        return False, True
    if isinstance(statement, ast.Raise | ast.Return):
        return False, False
    if isinstance(statement, ast.If):
        body = _sequence_outcome(statement.body)
        other = _sequence_outcome(statement.orelse) if statement.orelse else (True, False)
        return body[0] or other[0], body[1] or other[1]
    if isinstance(statement, ast.Match):
        outcomes = [_sequence_outcome(case.body) for case in statement.cases]
        exhaustive = any(
            isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None and case.guard is None
            for case in statement.cases
        )
        return not exhaustive or any(outcome[0] for outcome in outcomes), any(outcome[1] for outcome in outcomes)
    if isinstance(statement, ast.With | ast.AsyncWith):
        return _sequence_outcome(statement.body)
    if isinstance(statement, ast.Try | ast.TryStar):
        body_fallthrough, body_retry = _sequence_outcome(statement.body)
        normal_fallthrough, normal_retry = _sequence_outcome(statement.orelse)
        can_fall_through = body_fallthrough and normal_fallthrough
        can_retry = body_retry or (body_fallthrough and normal_retry)
        for nested_handler in statement.handlers:
            handler_fallthrough, handler_retry = _sequence_outcome(nested_handler.body)
            can_fall_through = can_fall_through or handler_fallthrough
            can_retry = can_retry or handler_retry
        if statement.finalbody:
            finally_fallthrough, finally_retry = _sequence_outcome(statement.finalbody)
            return can_fall_through and finally_fallthrough, finally_retry or (finally_fallthrough and can_retry)
        return can_fall_through, can_retry
    # A nested loop owns its own control flow and may complete.
    if isinstance(statement, ast.For | ast.AsyncFor | ast.While):
        return True, False
    return True, False


def _ancestors(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> list[ast.AST]:
    result: list[ast.AST] = []
    current = parents.get(node)
    while current is not None:
        result.append(current)
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            break
        current = parents.get(current)
    return result


def _handler_retries_loop(handler: ast.ExceptHandler) -> bool:
    can_fall_through, can_retry = _sequence_outcome(handler.body)
    return can_fall_through or can_retry


def _walk_same_scope(node: ast.AST) -> Iterator[ast.AST]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        yield from _walk_same_scope(child)


def _assigned_shields_awaited_by_try(
    loop: ast.For | ast.AsyncFor | ast.While,
    try_node: ast.Try | ast.TryStar,
    aliases: _ImportAliases,
    parents: dict[ast.AST, ast.AST],
) -> list[ast.Call]:
    awaited_names = {
        name.id
        for statement in try_node.body
        for child in _walk_same_scope(statement)
        if isinstance(child, ast.Await)
        for name in _walk_same_scope(child.value)
        if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load)
    }
    if not awaited_names:
        return []

    shield_calls: list[ast.Call] = []
    for child in _walk_same_scope(loop):
        if isinstance(child, ast.Assign):
            targets = child.targets
            value = child.value
        elif isinstance(child, ast.AnnAssign):
            targets = (child.target,)
            value = child.value
        else:
            continue
        if (
            isinstance(value, ast.Call)
            and _is_asyncio_shield(value, aliases, parents)
            and any(isinstance(target, ast.Name) and target.id in awaited_names for target in targets)
        ):
            shield_calls.append(value)
    return shield_calls


def _deferring_helper_aliases(module: ast.Module) -> frozenset[str]:
    names = set(_DEFERRING_HELPER_NAMES)
    for node in module.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("shared_future"):
            for imported in node.names:
                if imported.name in _DEFERRING_HELPER_NAMES:
                    names.add(imported.asname or imported.name)
    return frozenset(names)


def _callee_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_task_factory(call: ast.Call) -> bool:
    name = _callee_name(call)
    return name in _TASK_FACTORY_ATTRS or name in _PROBE_TASK_FACTORIES


def _is_iterator_step(call: ast.Call) -> bool:
    return _callee_name(call) in {"anext", "__anext__"}


def _function_defers_cancellation(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    helper_names: frozenset[str],
) -> bool:
    """The task body drives an async iterator or calls a deferring helper."""

    for statement in function.body:
        for child in _walk_same_scope(statement):
            if isinstance(child, ast.AsyncFor):
                return True
            if isinstance(child, ast.Call) and (_is_iterator_step(child) or _callee_name(child) in helper_names):
                return True
    return False


def _scope_chain(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> list[ast.AST]:
    """Enclosing function scopes of ``node`` (innermost first), then the module."""

    chain: list[ast.AST] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Module):
            chain.append(current)
        current = parents.get(current)
    return chain


def _local_definitions(scope: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Function definitions bound directly in ``scope`` (not inside nested scopes)."""

    definitions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    body = scope.body if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef | ast.Module) else []
    for statement in body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            definitions.setdefault(statement.name, statement)
            continue
        for child in _walk_same_scope(statement):
            for node in ast.iter_child_nodes(child):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    definitions.setdefault(node.name, node)
    return definitions


def _method_definitions(module: ast.Module) -> dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]:
    methods: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef):
            for statement in node.body:
                if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                    methods.setdefault(statement.name, []).append(statement)
    return methods


def _resolve_coroutine_function(
    call: ast.Call,
    parents: dict[ast.AST, ast.AST],
    methods: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]],
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Resolve ``f(...)`` / ``self.f(...)`` to the definitions it can bind to.

    Plain names resolve lexically: the innermost enclosing scope that defines
    the name wins, so a local ``worker`` shadows a module-level ``worker``.
    Attribute calls (``self.f``) cannot be resolved lexically; every method of
    that name in the module is considered so a deferring method is not missed.
    """

    if isinstance(call.func, ast.Name):
        for scope in _scope_chain(call, parents):
            definition = _local_definitions(scope).get(call.func.id)
            if definition is not None:
                return [definition]
        return []
    if isinstance(call.func, ast.Attribute):
        return methods.get(call.func.attr, [])
    return []


def _task_creation_defers(
    call: ast.Call,
    parents: dict[ast.AST, ast.AST],
    methods: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]],
    helper_names: frozenset[str],
) -> bool:
    if _callee_name(call) in _PROBE_TASK_FACTORIES:
        return True
    if not call.args:
        return False
    coroutine = call.args[0]
    if not isinstance(coroutine, ast.Call):
        return False
    if _is_iterator_step(coroutine):
        return True
    return any(
        _function_defers_cancellation(definition, helper_names)
        for definition in _resolve_coroutine_function(coroutine, parents, methods)
    )


def _is_task_annotation(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return False
    base = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    name = _qualified_name(base)
    return name[-1:] == ("Task",)


def _task_parameters(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Task-typed parameters lose their provenance at the call boundary.

    A caller may hand over a probe task whose coroutine defers cancellation,
    so a bare await of such a parameter is treated like a bare await of a
    known deferring task.
    """

    arguments = function.args
    return {
        parameter.arg
        for parameter in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
        if _is_task_annotation(parameter.annotation)
    }


def _is_asyncio_wait(call: ast.Call, aliases: _ImportAliases, parents: dict[ast.AST, ast.AST]) -> bool:
    name = _qualified_name(call.func)
    return (
        len(name) == 2
        and name[0] in aliases.asyncio_modules
        and name[1] == "wait"
        and not _shadowed_by_parameter(call, name[0], parents)
    ) or (len(name) == 1 and name[0] in aliases.asyncio_waits and not _shadowed_by_parameter(call, name[0], parents))


@dataclass(frozen=True, slots=True)
class _WaitSettlement:
    line: int
    done_name: str
    # Names waited on by ``asyncio.wait``; ``None`` when passed as a collection variable.
    waited: frozenset[str] | None


def _wait_settlements(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: _ImportAliases,
    parents: dict[ast.AST, ast.AST],
) -> list[_WaitSettlement]:
    """``done, _ = await asyncio.wait(...)`` bindings in ``function``."""

    settlements: list[_WaitSettlement] = []
    for statement in function.body:
        for child in _walk_same_scope(statement):
            if not isinstance(child, ast.Assign) or not isinstance(child.value, ast.Await):
                continue
            call = child.value.value
            if not (isinstance(call, ast.Call) and _is_asyncio_wait(call, aliases, parents) and call.args):
                continue
            target = child.targets[0] if len(child.targets) == 1 else None
            if not isinstance(target, ast.Tuple) or not target.elts or not isinstance(target.elts[0], ast.Name):
                continue
            waited = call.args[0]
            names: frozenset[str] | None = None
            if isinstance(waited, ast.Set | ast.List | ast.Tuple):
                names = frozenset(member.id for member in waited.elts if isinstance(member, ast.Name))
            settlements.append(_WaitSettlement(line=child.lineno, done_name=target.elts[0].id, waited=names))
    return settlements


def _node_within(node: ast.AST, statements: list[ast.stmt]) -> bool:
    return any(node is descendant for statement in statements for descendant in ast.walk(statement))


def _await_is_proven_settled(
    awaited: ast.Await,
    name: str,
    settlements: list[_WaitSettlement],
    parents: dict[ast.AST, ast.AST],
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """The await is dominated by an ``asyncio.wait`` proof that ``name`` is done.

    Accepted proofs, each requiring the ``asyncio.wait`` to precede the await:
    the await sits inside ``if name in done:``; the await is inside
    ``for name in done:``; or ``asyncio.wait`` waited exactly ``{name}`` and an
    earlier ``if not done:`` guard between the two raises or returns. A
    ``timeout`` or ``return_when`` argument never proves completion by itself.
    """

    prior = [settlement for settlement in settlements if settlement.line < awaited.lineno]
    if not prior:
        return False
    done_names = {settlement.done_name for settlement in prior}
    for ancestor in _ancestors(awaited, parents):
        if isinstance(ancestor, ast.If) and _node_within(awaited, ancestor.body):
            test = ancestor.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == name
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.In)
                and isinstance(test.comparators[0], ast.Name)
                and test.comparators[0].id in done_names
            ):
                return True
        if (
            isinstance(ancestor, ast.For)
            and isinstance(ancestor.target, ast.Name)
            and ancestor.target.id == name
            and isinstance(ancestor.iter, ast.Name)
            and ancestor.iter.id in done_names
            and _node_within(awaited, ancestor.body)
        ):
            return True
    for settlement in prior:
        if settlement.waited != frozenset({name}):
            continue
        for statement in function.body:
            for child in _walk_same_scope(statement):
                if (
                    isinstance(child, ast.If)
                    and settlement.line < child.lineno < awaited.lineno
                    and isinstance(child.test, ast.UnaryOp)
                    and isinstance(child.test.op, ast.Not)
                    and isinstance(child.test.operand, ast.Name)
                    and child.test.operand.id == settlement.done_name
                    and not _sequence_outcome(child.body)[0]
                ):
                    return True
    return False


def _direct_await_violations(
    path: Path,
    module: ast.Module,
    aliases: _ImportAliases,
    parents: dict[ast.AST, ast.AST],
    helper_names: frozenset[str],
) -> list[Violation]:
    methods = _method_definitions(module)
    violations: list[Violation] = []
    for function in ast.walk(module):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        deferring_tasks: set[str] = _task_parameters(function)
        for statement in function.body:
            for child in _walk_same_scope(statement):
                if isinstance(child, ast.Assign):
                    targets, value = child.targets, child.value
                elif isinstance(child, ast.AnnAssign):
                    targets, value = (child.target,), child.value
                else:
                    continue
                if (
                    isinstance(value, ast.Call)
                    and _is_task_factory(value)
                    and _task_creation_defers(value, parents, methods, helper_names)
                ):
                    deferring_tasks.update(target.id for target in targets if isinstance(target, ast.Name))
        if not deferring_tasks:
            continue
        settlements = _wait_settlements(function, aliases, parents)
        for statement in function.body:
            for child in _walk_same_scope(statement):
                if not (
                    isinstance(child, ast.Await)
                    and isinstance(child.value, ast.Name)
                    and child.value.id in deferring_tasks
                ):
                    continue
                if _await_is_proven_settled(child, child.value.id, settlements, parents, function):
                    continue
                violations.append(Violation(path=path, line=child.lineno, reason=_DIRECT_AWAIT_REASON))
    return violations


def find_violations(path: Path) -> list[Violation]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _import_aliases(module)
    parents = {child: parent for parent in ast.walk(module) for child in ast.iter_child_nodes(parent)}
    violations: list[Violation] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Try | ast.TryStar):
            continue
        loop = next(
            (
                ancestor
                for ancestor in _ancestors(node, parents)
                if isinstance(ancestor, ast.While | ast.For | ast.AsyncFor)
            ),
            None,
        )
        if loop is None:
            continue
        if not any(
            _catches_cancellation(handler, aliases, parents) and _handler_retries_loop(handler)
            for handler in node.handlers
        ):
            continue
        shield_calls = [
            child
            for statement in node.body
            for child in _walk_same_scope(statement)
            if isinstance(child, ast.Call) and _is_asyncio_shield(child, aliases, parents)
        ]
        if not shield_calls:
            shield_calls = _assigned_shields_awaited_by_try(loop, node, aliases, parents)
        if shield_calls:
            violations.append(Violation(path=path, line=shield_calls[0].lineno))
    helper_names = _deferring_helper_aliases(module)
    violations.extend(_direct_await_violations(path, module, aliases, parents, helper_names))
    return sorted(violations, key=lambda violation: violation.line)


def repository_violations(app_dir: Path | None = None) -> list[Violation]:
    target_dir = APP_DIR if app_dir is None else app_dir
    return [violation for path in sorted(target_dir.rglob("*.py")) for violation in find_violations(path)]


def main() -> int:
    violations = repository_violations()
    if not violations:
        print("cancellation safety checks passed")
        return 0
    for violation in violations:
        try:
            relative = violation.path.relative_to(ROOT)
        except ValueError:
            relative = violation.path
        print(
            f"cancellation safety check failed: {relative}:{violation.line}: {violation.reason}",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
