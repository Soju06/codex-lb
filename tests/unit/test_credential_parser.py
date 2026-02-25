from __future__ import annotations

import pytest

from app.modules.accounts.credential_parser import InvalidCredentialFormatError, parse_credential_lines

pytestmark = pytest.mark.unit


def test_parse_credential_lines_supports_colon_inside_password():
    parsed = parse_credential_lines(
        "alice@example.com:pa:ss:word:JBSWY3DPEHPK3PXP\n"
        "bob@example.com:plain:NB2W45DFOIZA====",
    )

    assert len(parsed) == 2
    assert parsed[0].line == 1
    assert parsed[0].email == "alice@example.com"
    assert parsed[0].account_password == "pa:ss:word"
    assert parsed[0].totp_secret == "JBSWY3DPEHPK3PXP"

    assert parsed[1].line == 2
    assert parsed[1].email == "bob@example.com"
    assert parsed[1].account_password == "plain"
    assert parsed[1].totp_secret == "NB2W45DFOIZA===="


def test_parse_credential_lines_rejects_empty_payload():
    with pytest.raises(InvalidCredentialFormatError, match="No credentials provided"):
        parse_credential_lines(" \n\t\n")


def test_parse_credential_lines_rejects_missing_totp_secret():
    with pytest.raises(InvalidCredentialFormatError, match="line 1"):
        parse_credential_lines("alice@example.com:password-only")
