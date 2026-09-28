"""The SCIM credential's pure rules: minting, digesting, displaying, coercing."""

from __future__ import annotations

import hashlib

import pytest

from app.modules.scim.schemas import coerce_active
from app.modules.scim.service import ScimChanges
from app.modules.scim.tokens import TOKEN_MARKER, generate_token, hash_token, token_prefix

pytestmark = pytest.mark.unit


def test_a_minted_token_is_unguessable_and_recognisable() -> None:
    first, second = generate_token(), generate_token()
    assert first != second
    assert first.startswith(f"{TOKEN_MARKER}_")
    # 32 random bytes in url-safe base64, so a digest of it has no dictionary
    # to attack — which is what makes an unsalted SHA-256 the right store.
    assert len(first.removeprefix(f"{TOKEN_MARKER}_")) >= 43


def test_the_stored_verifier_is_a_digest_and_the_prefix_is_not_the_secret() -> None:
    token = generate_token()
    assert hash_token(token) == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert len(hash_token(token)) == 64
    prefix = token_prefix(token)
    assert token.startswith(prefix)
    assert len(prefix) < len(token) // 2
    assert hash_token(token) != hash_token(token + "x")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("True", True),
        ("FALSE", False),
        (" false ", False),
        ("1", True),
        ("0", False),
        ("yes", None),
        (None, None),
        (1, None),
    ],
)
def test_active_is_read_from_both_spellings_identity_providers_send(value: object, expected: bool | None) -> None:
    assert coerce_active(value) is expected


def test_a_change_set_distinguishes_clearing_from_leaving_alone() -> None:
    """A PUT that omits an attribute clears it; a PATCH that omits one does not.

    Without that distinction every patch of ``active`` would also blank the
    display name and the e-mail of the account it touched.
    """

    untouched = ScimChanges()
    assert not untouched.has("displayName")

    cleared = untouched.with_value("displayName", display_name=None)
    assert cleared.has("displayName")
    assert cleared.display_name is None
    assert not cleared.has("emails")
