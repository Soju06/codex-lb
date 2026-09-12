"""Cross-language drift guard: the dashboard's request-log ``source`` literals vs the backend enum (#2123 WP-G).

WP-G shipped the ``source`` chip, the ``Source`` filter and the detail-dialog
block against two hand-copied string literals in
``frontend/src/features/dashboard/request-log-source.ts``. Nothing tied them to
``app/modules/proxy/overflow.py``, so a backend rename -- or a third overflow
source value -- would silently stop matching the UI: the chip would quietly stop
rendering and the filter would return nothing, with every test on both sides
still green. This file closes that residual, the way
``tests/unit/test_subscription_overflow_spec_delta.py`` already does for the spec
deltas and the operator docs.

The backend is the authority and is read by *import*, not by parsing:
``tests/unit/_overflow_constants.py`` discovers every module-level
``REQUEST_LOG_SOURCE_*`` string in ``overflow.py``, so adding a constant grows
the expected set without anyone remembering to update this file (the same
discovery feeds the spec-delta and dashboard-aggregate guards). The frontend
side is parsed out of the one module WP-G made the single closed mapping, and
the assertions are exact set equality in both directions:

* a backend value with no frontend literal fails (the new value would render as
  an unattributed row);
* a frontend literal the backend does not define fails (a rename left dead
  code behind, or a literal was typo'd);
* a value the filter does not offer, or that the chip cannot map to a kind,
  fails;
* a kind or filter option whose label is missing from any of en/ko/zh-CN fails.

``_parity_error`` and ``discovery_error`` are exercised directly against mutated
sets below, so the guards themselves are covered rather than merely trusted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.unit._overflow_constants import BACKEND_MODULE, SOURCE_PREFIX, discovery_error, overflow_constants

REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_SRC = REPO_ROOT / "frontend/src"
_SOURCE_MODULE = _FRONTEND_SRC / "features/dashboard/request-log-source.ts"
_DASHBOARD_PAGE = _FRONTEND_SRC / "features/dashboard/components/dashboard-page.tsx"
_LOCALES = _FRONTEND_SRC / "i18n/locales"
_LOCALE_NAMES = ("en.json", "ko.json", "zh-CN.json")

_FIX_HINT = (
    f"{BACKEND_MODULE} is the authority for request-log `source` values; "
    "update frontend/src/features/dashboard/request-log-source.ts (and the chip "
    "labels / filter options / locales that hang off it) to match"
)

_TS_LITERAL = re.compile(r'^export const (REQUEST_LOG_SOURCE_[A-Z0-9_]+) = "([^"]*)";$', re.MULTILINE)
_TS_KINDS = re.compile(r"^export const REQUEST_LOG_SOURCE_KINDS = \[([^\]]*)\] as const;$", re.MULTILINE)
_TS_FILTER_VALUES = re.compile(r"^export const REQUEST_LOG_SOURCE_FILTER_VALUES[^=\n]*= \[([^\]]*)\];$", re.MULTILINE)
_TS_SWITCH_CASE = re.compile(r'case (REQUEST_LOG_SOURCE_[A-Z0-9_]+):\s*\n\s*return "([A-Za-z0-9]+)";')
_TSX_FILTER_OPTION = re.compile(r'\{\s*value: (REQUEST_LOG_SOURCE_[A-Z0-9_]+),\s*label: t\("([^"]+)"\)\s*\}')
_TS_QUOTED = re.compile(r'"([^"]*)"')
_TS_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _frontend_literals() -> dict[str, str]:
    """Every exported ``REQUEST_LOG_SOURCE_*`` string constant, by TS name."""
    return dict(_TS_LITERAL.findall(_read(_SOURCE_MODULE)))


def _parity_error(backend: frozenset[str], frontend: frozenset[str]) -> str | None:
    """The loud message, or ``None`` when the two sides agree exactly."""
    missing = sorted(backend - frontend)
    unknown = sorted(frontend - backend)
    if not missing and not unknown:
        return None
    parts: list[str] = []
    if missing:
        parts.append(f"defined by {BACKEND_MODULE} but absent from {_rel(_SOURCE_MODULE)}: {missing}")
    if unknown:
        parts.append(f"spelled in {_rel(_SOURCE_MODULE)} but not defined by {BACKEND_MODULE}: {unknown}")
    return f"request-log `source` literals drifted -- {'; '.join(parts)}. {_FIX_HINT}."


def _sole_group(pattern: re.Pattern[str], text: str, label: str) -> str:
    matches = pattern.findall(text)
    assert len(matches) == 1, f"expected exactly one {label} declaration in {_rel(_SOURCE_MODULE)}, found {matches}"
    return matches[0]


def _locales() -> dict[str, dict[str, str]]:
    resources = {path.name: json.loads(_read(path)) for path in sorted(_LOCALES.glob("*.json"))}
    assert set(resources) == set(_LOCALE_NAMES), sorted(resources)
    return resources


BACKEND_LITERALS = overflow_constants(SOURCE_PREFIX)
LOCALES = _locales()


# -- the backend side is discoverable and publicly declared ------------------------------------


def test_backend_declares_its_request_log_source_literals() -> None:
    """Sanity-check the authority before comparing against it.

    Discovery is by name prefix, so a new value only reaches the guard if it is
    spelled and exported like the existing two. An empty, duplicated or private
    set would make every comparison below vacuously pass.
    """
    error = discovery_error(SOURCE_PREFIX, BACKEND_LITERALS)

    assert error is None, error


# -- backend <-> frontend literal parity, both directions --------------------------------------


def test_frontend_declares_exactly_the_backend_source_literals() -> None:
    frontend = _frontend_literals()
    assert frontend, f"failed to parse any exported REQUEST_LOG_SOURCE_* literal from {_rel(_SOURCE_MODULE)}"

    error = _parity_error(frozenset(BACKEND_LITERALS.values()), frozenset(frontend.values()))
    assert error is None, error


def test_filter_offers_every_source_literal() -> None:
    """A new value must reach the `Source` filter, not just the chip."""
    text = _read(_SOURCE_MODULE)
    offered = set(_TS_IDENTIFIER.findall(_sole_group(_TS_FILTER_VALUES, text, "REQUEST_LOG_SOURCE_FILTER_VALUES")))

    assert offered == set(_frontend_literals()), (
        f"REQUEST_LOG_SOURCE_FILTER_VALUES offers {sorted(offered)} but the module declares "
        f"{sorted(_frontend_literals())}; every attributable source value must be filterable"
    )


def test_every_source_literal_maps_to_its_own_chip_kind() -> None:
    """`requestLogSourceKind` must be a bijection onto the declared kinds."""
    text = _read(_SOURCE_MODULE)
    cases = dict(_TS_SWITCH_CASE.findall(text))
    declared_kinds = _TS_QUOTED.findall(_sole_group(_TS_KINDS, text, "REQUEST_LOG_SOURCE_KINDS"))

    assert set(cases) == set(_frontend_literals()), (
        f"requestLogSourceKind maps {sorted(cases)} but the module declares "
        f"{sorted(_frontend_literals())}; an unmapped value renders with no chip"
    )
    assert sorted(cases.values()) == sorted(declared_kinds), (
        f"requestLogSourceKind returns {sorted(cases.values())} but REQUEST_LOG_SOURCE_KINDS "
        f"declares {sorted(declared_kinds)}"
    )


# -- every kind and filter option is labelled in every locale ----------------------------------


def _declared_kinds() -> list[str]:
    return _TS_QUOTED.findall(_sole_group(_TS_KINDS, _read(_SOURCE_MODULE), "REQUEST_LOG_SOURCE_KINDS"))


@pytest.mark.parametrize("locale", _LOCALE_NAMES)
def test_every_chip_kind_is_labelled_in_every_locale(locale: str) -> None:
    """The chip label, its tooltip, and the detail-dialog reuse of the label."""
    resource = LOCALES[locale]

    for kind in _declared_kinds():
        for key in (f"dashboard.requests.source.{kind}", f"dashboard.requests.source.{kind}Title"):
            assert key in resource, f"{locale} is missing {key}"


@pytest.mark.parametrize("locale", _LOCALE_NAMES)
def test_every_filter_option_is_labelled_in_every_locale(locale: str) -> None:
    resource = LOCALES[locale]
    options = dict(_TSX_FILTER_OPTION.findall(_read(_DASHBOARD_PAGE)))

    assert set(options) == set(_frontend_literals()), (
        f"{_rel(_DASHBOARD_PAGE)} builds filter options for {sorted(options)} but "
        f"{_rel(_SOURCE_MODULE)} declares {sorted(_frontend_literals())}"
    )
    for constant, key in options.items():
        assert key in resource, f"{locale} is missing {key} (filter option for {constant})"


# -- the guard itself bites in both directions -------------------------------------------------

_BACKEND_SET = frozenset(BACKEND_LITERALS.values())
# Deliberately unspellable as a real ``source`` value / constant name, so these
# self-tests cannot collide with a value someone genuinely adds later.
_SYNTHETIC = "<synthetic source value>"
_SYNTHETIC_NAME = f"{SOURCE_PREFIX}<synthetic name>"


def test_guard_accepts_the_shipped_pair() -> None:
    assert _parity_error(_BACKEND_SET, _BACKEND_SET) is None


def test_guard_detects_a_backend_value_added_without_the_frontend() -> None:
    error = _parity_error(_BACKEND_SET | {_SYNTHETIC}, _BACKEND_SET)

    assert error is not None
    assert _SYNTHETIC in error
    assert "absent from" in error


def test_guard_detects_a_frontend_literal_removed_or_typoed() -> None:
    surviving = frozenset(sorted(_BACKEND_SET)[1:])
    error = _parity_error(_BACKEND_SET, surviving)

    assert error is not None
    assert sorted(_BACKEND_SET)[0] in error
    assert "absent from" in error


def test_guard_detects_a_renamed_backend_value() -> None:
    """A rename is an addition and a removal at once; both halves must be named."""
    old = sorted(_BACKEND_SET)[0]
    renamed = (_BACKEND_SET - {old}) | {_SYNTHETIC}
    error = _parity_error(renamed, _BACKEND_SET)

    assert error is not None
    assert _SYNTHETIC in error
    assert "absent from" in error
    assert "not defined by" in error


# -- the shared discovery refuses to hand out an untrustworthy set -----------------------------


def test_discovery_error_accepts_the_shipped_enum() -> None:
    assert discovery_error(SOURCE_PREFIX, BACKEND_LITERALS) is None


def test_discovery_error_detects_a_prefix_that_discovers_nothing() -> None:
    """A renamed prefix must fail loudly instead of making every comparison vacuous."""
    error = discovery_error(SOURCE_PREFIX, {})

    assert error is not None
    assert "vacuously" in error


def test_discovery_error_detects_a_constant_kept_out_of_dunder_all() -> None:
    error = discovery_error(SOURCE_PREFIX, {**BACKEND_LITERALS, _SYNTHETIC_NAME: _SYNTHETIC})

    assert error is not None
    assert _SYNTHETIC_NAME in error
    assert "__all__" in error


def test_discovery_error_detects_two_names_for_one_value() -> None:
    """A copy-pasted constant would otherwise shrink the expected set silently."""
    error = discovery_error(SOURCE_PREFIX, {**BACKEND_LITERALS, _SYNTHETIC_NAME: sorted(_BACKEND_SET)[0]})

    assert error is not None
    assert _SYNTHETIC_NAME in error
    assert "more than one name" in error
