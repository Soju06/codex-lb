"""One masking rule, for every surface that shows an address it may not reveal.

It lives here rather than beside its first caller because two places now apply
it to the same value and have to agree character for character: the account
list redacts an operator's address with it, and a refused company sign-in hands
the person the masked form of the address the identity provider asserted so an
administrator can match it against the refusal in the audit log. Two
implementations of "mask an e-mail" would eventually disagree, and the disagreement
would show up as a reference nobody can find.
"""

from __future__ import annotations


def mask_email(email: str) -> str:
    """Keep the first character of the local part and the domain: ``a***@example.com``."""

    local, sep, domain = email.partition("@")
    prefix = local[:1] if local else ""
    return f"{prefix}***{sep}{domain}" if sep else f"{prefix}***"
