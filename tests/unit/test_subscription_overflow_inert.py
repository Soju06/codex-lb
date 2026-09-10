"""Positive ratchet: where the subscription-overflow wiring lives, and where it must not (#2123 WP-C2, WP-D folded).

WP-B's inertness ratchet forbade the designation on the request path until the
decision was wired. This file replaces it with the *positive* shape of the
wiring so the ship-dark guarantee (design v3 I9) stays mechanical:

* the designation identifier is spelled only by the decision module
  (``app/modules/proxy/overflow.py``), its WebSocket parity helper, the two
  route files that call them (``api.py`` and the websocket mixin's two call
  sites), the metrics registry, the retention job (live-pin gauge) and the
  dashboard-side settings/model-source modules that already owned it;
* ``api.py`` reaches the feature only through ``app.modules.proxy.overflow``
  and calls each entry point from exactly the routes the design names:
  ``resolve_subscription_overflow`` from ``responses``/``v1_responses`` (after
  model/tier enforcement, before the subscription dispatch), ``compact_pin_denial``
  from ``_compact_responses`` (both compact routes, before any reservation),
  ``handshake_denial`` from the two websocket handshakes, and the
  ``not_portable_history`` hint from ``_logged_error_json_response``. The
  internal HTTP-bridge route never enters overflow (I2, D4);
* the pin primitive keeps a closed importer set, and ``SourceDispatch`` / the
  bulkhead claim are constructed only inside ``_source_responses_response`` (the
  overflow route helper hands the decision's claims through and never builds
  an owner of its own);
* ``service.py``, ``load_balancer.py`` and ``_service/**`` stay free of the
  identifiers, except the websocket helper module and the ``<= 2`` mixin call
  sites the WebSocket parity package adds.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"

_DESIGNATION_IDENTIFIERS = ("subscription_overflow",)
_PIN_IDENTIFIERS = (
    "ModelSourcePin",
    "model_source_pins",
    "PIN_IDLE_TTL",
    "PIN_TOMBSTONE_GRACE",
    "DRAIN_WINDOW",
)
_DESIGNATION_PATTERN = re.compile("|".join(re.escape(identifier) for identifier in _DESIGNATION_IDENTIFIERS))
_PIN_PATTERN = re.compile("|".join(re.escape(identifier) for identifier in _PIN_IDENTIFIERS))
_ANY_PATTERN = re.compile("|".join(re.escape(identifier) for identifier in _DESIGNATION_IDENTIFIERS + _PIN_IDENTIFIERS))

_API_MODULE = "app/modules/proxy/api.py"
_OVERFLOW_MODULE = "app/modules/proxy/overflow.py"
_WS_OVERFLOW_MODULE = "app/modules/proxy/_service/websocket/overflow.py"
_WS_MIXIN = "app/modules/proxy/_service/websocket/mixin.py"
_PIN_MODULE = "app/modules/proxy/model_source_pins.py"
_SOURCE_DISPATCH_MODULE = "app/modules/proxy/source_dispatch.py"
_RETENTION_JOB = "app/core/retention/job.py"
_METRICS_MODULE = "app/core/metrics/prometheus.py"

_DASHBOARD_READERS = frozenset(
    {
        "app/db/models.py",
        "app/modules/settings/api.py",
        "app/modules/settings/repository.py",
        "app/modules/settings/schemas.py",
        "app/modules/settings/service.py",
        "app/modules/settings/subscription_overflow.py",
        "app/modules/model_sources/api.py",
    }
)
_ALEMBIC_PREFIX = "app/db/alembic/versions/"

# Production files that may spell the designation identifier.
_DESIGNATION_READERS = _DASHBOARD_READERS | frozenset(
    {
        _API_MODULE,
        _OVERFLOW_MODULE,
        _WS_OVERFLOW_MODULE,
        _PIN_MODULE,
        _METRICS_MODULE,
        _RETENTION_JOB,
    }
)
# Production files that may name the pin table / pin types.
_PIN_READERS = _DASHBOARD_READERS | frozenset(
    {
        _PIN_MODULE,
        _SOURCE_DISPATCH_MODULE,
        _OVERFLOW_MODULE,
        _WS_OVERFLOW_MODULE,
        _RETENTION_JOB,
    }
)
# ``import app.modules.proxy.model_source_pins`` is allowed here and nowhere else.
_PIN_IMPORTERS_REQUIRED = frozenset({_RETENTION_JOB, _SOURCE_DISPATCH_MODULE, _OVERFLOW_MODULE})
_PIN_IMPORTERS_ALLOWED = _PIN_IMPORTERS_REQUIRED | {_WS_OVERFLOW_MODULE}

# api.py's single overflow import and the entry points it wires (design v3 §4.1, §7.2, §7.3, §8.7).
_API_OVERFLOW_IMPORT = "app.modules.proxy.overflow"
_API_OVERFLOW_NAMES = frozenset(
    {
        "ROUTE_CODEX_RESPONSES",
        "ROUTE_V1_RESPONSES",
        "OverflowDispatch",
        "apply_usage_limit_hint",
        "compact_pin_denial",
        "handshake_denial",
        "resolve_subscription_overflow",
        "restore_client_store",
    }
)
_API_CALL_SITES = {
    "resolve_subscription_overflow": {"responses", "v1_responses"},
    "_overflow_source_response": {"responses", "v1_responses"},
    "compact_pin_denial": {"_compact_responses"},
    "handshake_denial": {"responses_websocket", "v1_responses_websocket"},
    "apply_usage_limit_hint": {"_logged_error_json_response"},
}
_OVERFLOW_FREE_ROUTES = ("internal_bridge_responses",)

# The WebSocket parity package: one helper module, at most two mixin call sites.
_WS_HELPER_IMPORT = "app.modules.proxy._service.websocket.overflow"
_WS_MIXIN_CALL_SITES = frozenset({"bounce_exhausted_websocket_turn", "bounce_pinned_or_anchored_websocket_turn"})
_WS_MIXIN_MAX_CALLS = 2

_HOT_PATH_FILES = (
    APP_DIR / "modules" / "proxy" / "service.py",
    APP_DIR / "modules" / "proxy" / "load_balancer.py",
)
_SERVICE_DIR = APP_DIR / "modules" / "proxy" / "_service"


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _mentions(path: Path, pattern: re.Pattern[str]) -> list[str]:
    hits: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if pattern.search(line):
            hits.append(f"{_relative(path)}:{line_number}: {line.strip()}")
    return hits


def _app_modules() -> list[Path]:
    return sorted(APP_DIR.rglob("*.py"))


def _api_module() -> ast.Module:
    return ast.parse((REPO_ROOT / _API_MODULE).read_text(encoding="utf-8"))


def _parents(module: ast.Module) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(module) for child in ast.iter_child_nodes(parent)}


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            return current.name
        current = parents.get(current)
    return None


def _call_sites(module: ast.Module, name: str) -> list[tuple[str | None, int]]:
    parents = _parents(module)
    return [
        (_enclosing_function(node, parents), node.lineno)
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def _function(module: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in module.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not a top-level function of api.py")


def _first_call_line(function: ast.AST, name: str) -> int:
    lines = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]
    assert lines, f"{name} is not called"
    return min(lines)


def _imports_of(module: ast.Module, module_name: str) -> list[ast.ImportFrom]:
    return [
        node for node in ast.walk(module) if isinstance(node, ast.ImportFrom) and (node.module or "") == module_name
    ]


# -- designation and pin identifiers ------------------------------------------------------------------


def test_designation_is_spelled_only_by_the_wiring_and_the_dashboard_modules() -> None:
    offenders: list[str] = []
    readers: set[str] = set()
    for path in _app_modules():
        relative = _relative(path)
        hits = _mentions(path, _DESIGNATION_PATTERN)
        if not hits:
            continue
        readers.add(relative)
        if relative in _DESIGNATION_READERS or relative.startswith(_ALEMBIC_PREFIX):
            continue
        offenders.extend(hits)
    assert offenders == [], "Unexpected reader of the subscription-overflow designation:\n" + "\n".join(offenders)
    # Positive half: the wiring exists where the design puts it.
    assert {_OVERFLOW_MODULE, _API_MODULE} <= readers, sorted(readers)


def test_pin_identifiers_stay_inside_the_pin_readers() -> None:
    offenders: list[str] = []
    for path in _app_modules():
        relative = _relative(path)
        if relative in _PIN_READERS or relative.startswith(_ALEMBIC_PREFIX):
            continue
        offenders.extend(_mentions(path, _PIN_PATTERN))
    assert offenders == [], "Unexpected reader of the model-source pin table/types:\n" + "\n".join(offenders)


def test_pin_module_importers_are_the_decision_the_owner_the_ws_helper_and_the_retention_job() -> None:
    importers = {
        _relative(path)
        for path in _app_modules()
        if _relative(path) != _PIN_MODULE and "app.modules.proxy.model_source_pins" in path.read_text(encoding="utf-8")
    }
    assert importers <= _PIN_IMPORTERS_ALLOWED, sorted(importers - _PIN_IMPORTERS_ALLOWED)
    assert _PIN_IMPORTERS_REQUIRED <= importers, sorted(_PIN_IMPORTERS_REQUIRED - importers)


def test_allowlisted_modules_exist_so_the_allowlists_cannot_rot() -> None:
    for relative in sorted(_DESIGNATION_READERS | _PIN_READERS | _PIN_IMPORTERS_ALLOWED):
        assert (REPO_ROOT / relative).is_file(), relative
    assert any((REPO_ROOT / _ALEMBIC_PREFIX).glob("*_add_subscription_overflow.py"))


def test_dashboard_helper_module_is_imported_only_by_the_dashboard_modules_and_the_pin_module() -> None:
    importers = [
        _relative(path)
        for path in _app_modules()
        if _relative(path) not in _DASHBOARD_READERS
        and _relative(path) != _PIN_MODULE
        and "app.modules.settings.subscription_overflow" in path.read_text(encoding="utf-8")
    ]
    assert importers == []


# -- api.py wiring ------------------------------------------------------------------------------------------


def test_api_reaches_the_feature_only_through_the_decision_module() -> None:
    module = _api_module()
    overflow_imports = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
        and "overflow" in (node.module or "")
        or (isinstance(node, ast.Import) and any("overflow" in alias.name for alias in node.names))
    ]
    assert len(overflow_imports) == 1, [ast.dump(node) for node in overflow_imports]
    (overflow_import,) = overflow_imports
    assert isinstance(overflow_import, ast.ImportFrom)
    assert overflow_import.module == _API_OVERFLOW_IMPORT
    assert {alias.name for alias in overflow_import.names} == _API_OVERFLOW_NAMES
    assert all(alias.asname is None for alias in overflow_import.names)
    api_source = (REPO_ROOT / _API_MODULE).read_text(encoding="utf-8")
    assert "model_source_pins" not in api_source, "api.py must not reach the pin primitive directly"
    assert _WS_HELPER_IMPORT not in api_source, "the websocket helper is the mixin's, not the route file's"


def test_each_overflow_entry_point_is_called_from_exactly_the_designed_routes() -> None:
    module = _api_module()
    for name, expected in _API_CALL_SITES.items():
        sites = _call_sites(module, name)
        callers = [caller for caller, _line in sites]
        assert set(callers) == expected, (name, sites)
        # One call per route: no second decision, no second denial.
        assert len(callers) == len(set(callers)), (name, sites)


def test_the_bridge_route_never_enters_overflow() -> None:
    module = _api_module()
    for route in _OVERFLOW_FREE_ROUTES:
        function = _function(module, route)
        names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
        assert names.isdisjoint(_API_OVERFLOW_NAMES | {"_overflow_source_response"}), (route, sorted(names))
        assert not _ANY_PATTERN.search(ast.get_source_segment((REPO_ROOT / _API_MODULE).read_text(), function) or "")


def test_the_decision_sits_after_tier_enforcement_and_before_the_subscription_dispatch() -> None:
    """Probe inputs == selection inputs, and nothing is reserved, leased or written before the decision (§4.1)."""

    module = _api_module()
    for route in ("responses", "v1_responses"):
        function = _function(module, route)
        tier_fallback = _first_call_line(function, "apply_enforced_service_tier_model_fallback")
        decision = _first_call_line(function, "resolve_subscription_overflow")
        overflow_dispatch = _first_call_line(function, "_overflow_source_response")
        stream = _first_call_line(function, "_stream_responses")
        collect = _first_call_line(function, "_collect_responses")
        assert tier_fallback < decision < overflow_dispatch < min(stream, collect), (
            route,
            tier_fallback,
            decision,
            overflow_dispatch,
            stream,
            collect,
        )


def test_owner_and_claims_are_built_only_inside_the_source_route_helper() -> None:
    """The overflow route helper hands the decision's claims through; it never claims or builds an owner (I13)."""

    module = _api_module()
    for name in ("SourceDispatch", "try_claim_source_admission"):
        callers = {caller for caller, _line in _call_sites(module, name)}
        assert callers == {"_source_responses_response"}, (name, callers)
    parents = _parents(module)
    latch_sites = {
        _enclosing_function(node, parents)
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "release_if_unowned"
    }
    assert latch_sites == {"_overflow_source_response", "_source_responses_response"}, latch_sites


def test_the_overflow_route_helper_is_a_latched_pass_through() -> None:
    module = _api_module()
    helper = _function(module, "_overflow_source_response")
    body = helper.body
    # docstring, two payload assignments, one try/finally
    trailing = body[-1]
    assert isinstance(trailing, ast.Try), ast.dump(trailing)
    assert trailing.finalbody, "the claims latch is the finally block"
    finally_calls = [
        node.func.attr
        for node in ast.walk(trailing.finalbody[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert finally_calls == ["release_if_unowned"], finally_calls
    forwarded = [
        node
        for node in ast.walk(trailing)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_source_responses_response"
    ]
    assert len(forwarded) == 1
    assert {keyword.arg for keyword in forwarded[0].keywords} >= {"overflow", "source", "context"}


# -- hot-path modules outside the wiring ---------------------------------------------------------------------


def test_service_and_load_balancer_and_service_package_never_mention_the_identifiers() -> None:
    paths = list(_HOT_PATH_FILES) + sorted(_SERVICE_DIR.rglob("*.py"))
    hits: list[str] = []
    for path in paths:
        if _relative(path) == _WS_OVERFLOW_MODULE:
            continue
        hits.extend(_mentions(path, _ANY_PATTERN))
    assert hits == [], "Overflow identifiers leaked outside the wiring:\n" + "\n".join(hits)


def test_websocket_mixin_uses_at_most_the_two_parity_call_sites() -> None:
    mixin_path = REPO_ROOT / _WS_MIXIN
    module = ast.parse(mixin_path.read_text(encoding="utf-8"))
    helper_imports = _imports_of(module, _WS_HELPER_IMPORT)
    imported = {alias.asname or alias.name for node in helper_imports for alias in node.names}
    assert imported <= _WS_MIXIN_CALL_SITES, sorted(imported)
    calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _WS_MIXIN_CALL_SITES
    ]
    assert len(calls) <= _WS_MIXIN_MAX_CALLS, [call.lineno for call in calls]
    assert {call.func.id for call in calls if isinstance(call.func, ast.Name)} <= imported
    # The mixin never reaches the decision module or the pin primitive directly.
    mixin_source = mixin_path.read_text(encoding="utf-8")
    assert _API_OVERFLOW_IMPORT not in mixin_source
    assert "model_source_pins" not in mixin_source


def test_only_the_websocket_helper_imports_the_decision_module_from_the_service_package() -> None:
    importers = {
        _relative(path)
        for path in sorted(_SERVICE_DIR.rglob("*.py"))
        if _API_OVERFLOW_IMPORT in path.read_text(encoding="utf-8")
    }
    assert importers <= {_WS_OVERFLOW_MODULE}, sorted(importers)
    ws_helper_importers = {
        _relative(path)
        for path in _app_modules()
        if _relative(path) != _WS_OVERFLOW_MODULE and _WS_HELPER_IMPORT in path.read_text(encoding="utf-8")
    }
    assert ws_helper_importers <= {_WS_MIXIN}, sorted(ws_helper_importers)


# -- shape of the individual hunks ---------------------------------------------------------------------------


def _statement_index(body: list[ast.stmt], predicate: Callable[[ast.stmt], bool]) -> int:
    for index, statement in enumerate(body):
        if predicate(statement):
            return index
    raise AssertionError("statement not found")


def _calls_in(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == name
    ]


def _if_blocks(function: ast.AST) -> list[ast.If]:
    return [node for node in ast.walk(function) if isinstance(node, ast.If)]


@pytest.mark.parametrize("route", ["responses_websocket", "v1_responses_websocket"])
def test_websocket_handshake_denial_sits_in_the_capability_exemption_right_after_the_transport_denial(
    route: str,
) -> None:
    """CP-8: capability handshakes are never downgraded; the overflow 426 follows the upstream-transport 426."""

    module = _api_module()
    function = _function(module, route)
    blocks = [block for block in _if_blocks(function) if _calls_in(block, "handshake_denial")]
    assert len(blocks) == 1, "the overflow handshake denial must live in exactly one ``if`` block"
    (block,) = blocks
    # ``if not capability_header_values:`` guards both denials.
    test = block.test
    assert isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
    assert isinstance(test.operand, ast.Name) and test.operand.id == "capability_header_values"
    transport = _statement_index(block.body, lambda s: bool(_calls_in(s, "_websocket_upstream_transport_denial")))
    overflow = _statement_index(block.body, lambda s: bool(_calls_in(s, "handshake_denial")))
    assert transport < overflow
    # The denial is sent with ``send_denial_response`` and the handler returns before ``accept``.
    denial_branch = block.body[overflow + 1]
    assert isinstance(denial_branch, ast.If)
    branch_calls = {
        call.func.attr
        for call in ast.walk(denial_branch)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    }
    assert "send_denial_response" in branch_calls
    assert any(isinstance(statement, ast.Return) for statement in denial_branch.body)
    accept = _statement_index(
        function.body,
        lambda s: any(
            isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "accept"
            for call in ast.walk(s)
        ),
    )
    guard = _statement_index(function.body, lambda s: s is block)
    assert guard < accept


def test_compaction_pin_denial_precedes_the_admission_estimate() -> None:
    module = _api_module()
    function = _function(module, "_compact_responses")
    access = _statement_index(function.body, lambda s: bool(_calls_in(s, "validate_model_access")))
    denial = _statement_index(function.body, lambda s: bool(_calls_in(s, "compact_pin_denial")))
    estimate = _statement_index(function.body, lambda s: bool(_calls_in(s, "estimate_api_key_request_usage")))
    assert access < denial < estimate


def test_non_stream_source_dispatch_commits_the_resolved_intent_before_the_success_finish() -> None:
    """Anchor wiring (§3, §6.5): the pin intent is resolved against the source response id and committed once."""

    module = _api_module()
    function = _function(module, "_finish_non_stream_source_dispatch")
    resolves = [
        call
        for call in ast.walk(function)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "resolve"
    ]
    assert len(resolves) == 1
    (resolve,) = resolves
    assert ast.unparse(resolve) == "owner.pin_intent.resolve(owner.source_response_id)"
    commits = [
        call
        for call in ast.walk(function)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "commit"
    ]
    assert len(commits) == 1
    assert ast.unparse(commits[0].func) == "owner.pin_executor.commit"
    assert {keyword.arg for keyword in commits[0].keywords} == {"drain_until", "scheduler", "clock"}
    source = ast.get_source_segment((REPO_ROOT / _API_MODULE).read_text(encoding="utf-8"), function) or ""
    assert source.index(".resolve(") < source.index('status="success"')


def test_only_the_usage_limit_429_reaches_the_hint_hook() -> None:
    module = _api_module()
    function = _function(module, "_logged_error_json_response")
    blocks = [block for block in _if_blocks(function) if _calls_in(block, "apply_usage_limit_hint")]
    assert len(blocks) == 1
    assert ast.unparse(blocks[0].test) == "status_code == 429 and code == USAGE_LIMIT_REACHED"
