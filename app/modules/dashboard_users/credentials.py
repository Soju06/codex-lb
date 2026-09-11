"""The credential-required invariant, shared by the self-service and management paths."""

from __future__ import annotations


class CredentialRequiredError(ValueError):
    pass


def assert_credential_remains(*, password_hash: str | None, identity_count: int, solo_install: bool) -> None:
    """Refuse a write that would leave an account with neither a password nor an external identity.

    The one exception is the single-account install removing its own password:
    that deliberately returns the install to the passwordless bootstrap state.
    """

    if password_hash is None and identity_count == 0 and not solo_install:
        raise CredentialRequiredError("The account would be left without any way to sign in")
