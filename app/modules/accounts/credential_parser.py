from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CredentialLine:
    line: int
    email: str
    account_password: str
    totp_secret: str


class InvalidCredentialFormatError(ValueError):
    pass


def parse_credential_lines(credentials_text: str) -> list[CredentialLine]:
    parsed: list[CredentialLine] = []
    for line_number, raw_line in enumerate(credentials_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        first_delimiter = line.find(":")
        last_delimiter = line.rfind(":")
        if first_delimiter <= 0 or last_delimiter <= first_delimiter or last_delimiter >= len(line) - 1:
            raise InvalidCredentialFormatError(
                f"Invalid credentials format at line {line_number}. Expected <email>:<secret>:<2fa_secret>.",
            )

        email = line[:first_delimiter].strip()
        account_password = line[first_delimiter + 1 : last_delimiter]
        totp_secret = line[last_delimiter + 1 :].strip()

        if not email or "@" not in email:
            raise InvalidCredentialFormatError(f"Invalid email at line {line_number}.")
        if not account_password:
            raise InvalidCredentialFormatError(f"Secret is required at line {line_number}.")
        if not totp_secret:
            raise InvalidCredentialFormatError(f"2FA secret is required at line {line_number}.")

        parsed.append(
            CredentialLine(
                line=line_number,
                email=email,
                account_password=account_password,
                totp_secret=totp_secret,
            ),
        )

    if not parsed:
        raise InvalidCredentialFormatError("No credentials provided.")

    return parsed
