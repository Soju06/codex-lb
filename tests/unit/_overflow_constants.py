"""Dynamic discovery of the closed string enums ``app/modules/proxy/overflow.py`` defines (#2123).

``overflow.py`` is the authority for the overflow feature's closed string
enums: the ``request_logs.source`` values an overflow dispatch writes, the
``route`` labels of ``codex_lb_subscription_overflow_total``, and the dispatch
kinds. Drift guards that spell those constants *by name* are a hole -- they pin
the values that exist today and say nothing about a value added tomorrow, so a
third ``source`` could reach the request log without any normative delta, the
operator docs or the dashboard's ``IN`` predicate learning about it, with every
test on both sides still green.

Discovery here is by *import* plus name prefix, so one new constant grows every
expected set at once. Requiring a ``str`` value keeps same-prefix aggregates
(the frontend module deliberately exports a ``REQUEST_LOG_SOURCE_KINDS`` array;
a backend tuple would be the same shape) out of the discovered enum instead of
silently widening it.

This module holds no tests, so importing it from a test module does not couple
two test files' collection order -- the same reason
``tests/unit/_proxy_test_helpers.py`` carries a leading underscore.
"""

from __future__ import annotations

import re

from app.modules.proxy import overflow

BACKEND_MODULE = "app/modules/proxy/overflow.py"

SOURCE_PREFIX = "REQUEST_LOG_SOURCE_"
ROUTE_PREFIX = "ROUTE_"
DISPATCH_KIND_PREFIX = "DISPATCH_KIND_"

_SUFFIX = re.compile(r"[A-Z0-9_]+")


def overflow_constants(prefix: str) -> dict[str, str]:
    """Every module-level ``<prefix>*`` string constant of ``overflow.py``, by name."""
    return {
        name: value
        for name, value in vars(overflow).items()
        if name.startswith(prefix) and _SUFFIX.fullmatch(name.removeprefix(prefix)) and isinstance(value, str)
    }


def discovery_error(prefix: str, constants: dict[str, str]) -> str | None:
    """The loud message when a discovered set cannot be trusted, else ``None``."""
    if not constants:
        return (
            f"no {prefix}* string constants found in {BACKEND_MODULE}; every {prefix}* drift guard would pass vacuously"
        )
    values = list(constants.values())
    duplicates = sorted(name for name, value in constants.items() if values.count(value) > 1)
    if duplicates:
        return f"{BACKEND_MODULE} gives the same {prefix}* value to more than one name: {duplicates}"
    undeclared = sorted(set(constants) - set(overflow.__all__))
    if undeclared:
        return (
            f"{BACKEND_MODULE} defines {undeclared} without listing them in __all__; "
            f"the {prefix}* drift guards discover the enum through the public surface"
        )
    return None
