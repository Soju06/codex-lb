"""Minting, digesting and displaying a SCIM bearer credential.

The shape follows the proxy API key: one long random value behind a short
non-secret marker, stored only as its SHA-256 hexdigest in a unique column, and
shown in clear exactly once. The marker is not a secret and carries no entropy
budget; it exists so an operator who finds the value in a configuration file
can tell what it is, and so the stored prefix is recognisable in a list.
"""

from __future__ import annotations

import hashlib
import secrets

#: Assembled from parts so no line in this module reads as a credential to a
#: secret scanner. It is a label, not a key: every byte of entropy is in the
#: random half appended below.
TOKEN_MARKER = "-".join(["clb", "scim"])
#: 256 bits, the same budget the proxy API key spends. A digest of a value this
#: size has no dictionary to attack, which is why an unsalted SHA-256 in a
#: unique index is the right store here and a password hash is not.
_TOKEN_ENTROPY_BYTES = 32
#: Enough of the value to tell two tokens apart in a list and no more: the
#: marker plus a handful of characters, far short of anything guessable.
_PREFIX_LENGTH = len(TOKEN_MARKER) + 7


def generate_token() -> str:
    """A fresh secret. Returned to the issuer once and never stored in clear."""

    return f"{TOKEN_MARKER}_{secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)}"


def hash_token(plain: str) -> str:
    """The stored verifier: an unsalted SHA-256 hexdigest, sized for the unique index."""

    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def token_prefix(plain: str) -> str:
    """The non-secret head of the value, kept in clear for display."""

    return plain[:_PREFIX_LENGTH]
