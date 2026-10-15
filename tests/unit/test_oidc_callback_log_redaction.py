"""The OIDC callback's credentials, through the logging path that actually renders them.

The handler logs nothing. That is not the same as nothing being logged: with
Uvicorn's default text access logging every request is rendered as its full
request line, query string included, and this route's query string is an
authorization code plus the flow's ``state``. So the assertions here run the
real ``UtcAccessFormatter`` over a real access record rather than calling the
redaction helper directly.

A *refused* callback matters most. Its code was never exchanged, so it is still
live at the identity provider, and refusals are exactly the requests an
operator goes looking for in the access log.
"""

from __future__ import annotations

import logging

import pytest

from app.core.runtime_logging import UtcAccessFormatter, redact_rendered_log_text

pytestmark = pytest.mark.unit

CALLBACK = "/api/dashboard-auth/oidc/callback"
# Assembled, not written out: a literal of this shape reads as a credential.
CODE = "authorization-" + "code-0123456789abcdef"
STATE = "flow-state-" + "0123456789abcdefghij"
_REDACTED = "[REDACTED]"

#: Uvicorn's own default, minus the colours.
_ACCESS_FORMAT = '%(client_addr)s - "%(request_line)s" %(status_code)s'


def _access_line(path: str, status: int = 303) -> str:
    """One ``uvicorn.access`` record, formatted the way the server formats it."""

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:54321", "GET", path, "1.1", status),
        exc_info=None,
    )
    return UtcAccessFormatter(_ACCESS_FORMAT, use_colors=False).format(record)


@pytest.mark.parametrize("status", [303, 400, 429], ids=["completed", "refused", "rate-limited"])
def test_the_callback_query_never_reaches_the_access_log(status: int) -> None:
    line = _access_line(f"{CALLBACK}?state={STATE}&code={CODE}", status=status)

    assert CODE not in line and STATE not in line
    assert "code=[REDACTED]" in line and "state=[REDACTED]" in line
    # Still a readable access line: method, path and status survive.
    assert CALLBACK in line and str(status) in line


def test_an_error_from_the_identity_provider_stays_readable() -> None:
    """Only the two credentials are masked; the parameter an operator needs is not."""

    line = _access_line(f"{CALLBACK}?error=access_denied&state={STATE}")

    assert "error=access_denied" in line
    assert STATE not in line and "state=[REDACTED]" in line


def test_the_router_reads_an_encoded_name_as_the_parameter() -> None:
    """Why the encoded spellings below matter: they are the same request to the route.

    This is the mechanism, asserted rather than assumed -- if Starlette ever
    stopped decoding names, the extra spellings would stop being reachable and
    this test would say so before the redaction test did.
    """

    from starlette.datastructures import QueryParams

    parameters = QueryParams(f"c%6Fde={CODE}&%73tate={STATE}")
    assert parameters["code"] == CODE and parameters["state"] == STATE


@pytest.mark.parametrize(
    "query",
    [
        f"c%6Fde={CODE}&%73tate={STATE}",
        f"%63%6f%64%65={CODE}&%53%54%41%54%45={STATE}",
        f"CODE={CODE};STATE={STATE}",
        f"error=access_denied&c%6fde={CODE}&state={STATE}",
    ],
    ids=["mixed-encoding", "fully-encoded", "uppercase", "after-another-parameter"],
)
def test_an_encoded_parameter_name_is_still_the_parameter(query: str) -> None:
    """Starlette percent-decodes query *names*, so the mask has to as well.

    ``request.query_params`` is built with ``parse_qsl``, which unquotes names:
    ``?c%6Fde=`` binds to the handler's ``code`` exactly as ``?code=`` does.
    Uvicorn meanwhile renders the request line from the raw ``query_string``
    bytes, encoding intact, so a literal-key pattern masks one spelling of a
    parameter the route accepts in several.
    """

    line = _access_line(f"{CALLBACK}?{query}")

    assert CODE not in line and STATE not in line
    assert line.count(_REDACTED) == 2


def test_a_parameter_that_only_looks_encoded_stays_readable() -> None:
    """Only names that *decode* to the two credentials are masked."""

    line = _access_line(f"{CALLBACK}?session_state=abc&decode=1&statement=2")

    assert "session_state=abc" in line
    assert "decode=1" in line and "statement=2" in line
    assert _REDACTED not in line


def test_the_rule_is_anchored_to_this_one_route() -> None:
    """No other route's ``code`` or ``state`` parameter is touched."""

    elsewhere = "/api/model-sources?state=active&code=gpt"
    assert redact_rendered_log_text(elsewhere) == elsewhere
    assert "code=gpt" in _access_line(elsewhere, status=200)


@pytest.mark.parametrize("keyed", [True, False], ids=["warning-and-above", "info-and-below"])
def test_the_masking_does_not_depend_on_the_record_level(keyed: bool) -> None:
    """Access logs are INFO, where the costly keyed pass is skipped by design."""

    line = f'"GET {CALLBACK}?code={CODE}&state={STATE} HTTP/1.1" 303'
    redacted = redact_rendered_log_text(line, keyed_secrets=keyed)
    assert CODE not in redacted and STATE not in redacted


def test_a_callback_url_in_an_error_field_is_masked_too() -> None:
    """Structured error logs carry the path as a field, not only as a request line."""

    from app.core.runtime_logging import _redact_log_value

    assert _redact_log_value(f"{CALLBACK}?code={CODE}") == f"{CALLBACK}?code=[REDACTED]"
