from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

BUNDLE_FORMAT = "codex-lb-account-bundle"
BUNDLE_VERSION = 1
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32
MAX_BUNDLE_ACCOUNTS = 10_000


class AccountBundleError(ValueError):
    code = "invalid_account_bundle"


class UnsupportedAccountBundleError(AccountBundleError):
    code = "unsupported_account_bundle"


class AccountBundleTooLargeError(AccountBundleError):
    code = "payload_too_large"


class BundleCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_mode: Literal["chatgpt"] = "chatgpt"
    openai_api_key: str | None = None
    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    id_token: str = Field(min_length=1)

    @field_validator("access_token", "refresh_token", "id_token")
    @classmethod
    def reject_blank_credentials(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("credential cannot be blank")
        return value

    @model_validator(mode="after")
    def reject_api_key_for_chatgpt(self) -> BundleCredentials:
        if self.openai_api_key is not None:
            raise ValueError("chatgpt credentials cannot contain an API key")
        return self


class BundleAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chatgpt_account_id: str | None = None
    chatgpt_user_id: str | None = None
    email: str = Field(min_length=1)
    workspace_id: str | None = None
    workspace_label: str | None = None
    seat_type: str | None = None
    alias: str | None = Field(default=None, max_length=255)
    plan_type: str = Field(min_length=1)
    routing_policy: Literal["normal", "burn_first", "preserve"] = "normal"
    limit_warmup_enabled: bool = False
    security_work_authorized: bool = False
    credentials: BundleCredentials

    @field_validator("email")
    @classmethod
    def reject_blank_email(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("email cannot be blank")
        return value

    @field_validator(
        "chatgpt_account_id",
        "chatgpt_user_id",
        "workspace_id",
        "workspace_label",
        "seat_type",
        "alias",
        mode="before",
    )
    @classmethod
    def reject_blank_optional_strings(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class AccountBundlePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    created_at: datetime
    accounts: list[BundleAccount] = Field(max_length=MAX_BUNDLE_ACCOUNTS)

    @model_validator(mode="after")
    def reject_duplicate_identities(self) -> AccountBundlePayload:
        seen_canonical_ids: set[tuple[str, str | None, str]] = set()
        seen_canonical_aliases: set[tuple[str, str | None, str]] = set()
        seen_legacy_keys: set[tuple[str, str | None, str | None]] = set()
        for account in self.accounts:
            identity_prefix = (account.email.lower(), account.chatgpt_account_id)
            if account.workspace_id is None:
                legacy_key = (*identity_prefix, account.workspace_label)
                if legacy_key in seen_legacy_keys or legacy_key in seen_canonical_aliases:
                    raise ValueError("bundle contains duplicate account identities")
                seen_legacy_keys.add(legacy_key)
                continue

            canonical_id = (*identity_prefix, account.workspace_id)
            aliases = {account.workspace_id}
            if account.workspace_label is not None:
                aliases.add(account.workspace_label)
            canonical_aliases = {(*identity_prefix, alias) for alias in aliases}
            if canonical_id in seen_canonical_ids or not seen_legacy_keys.isdisjoint(canonical_aliases):
                raise ValueError("bundle contains duplicate account identities")
            seen_canonical_ids.add(canonical_id)
            seen_canonical_aliases.update(canonical_aliases)
        return self


class ScryptDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["scrypt"]
    salt: str
    n: Literal[32768]
    r: Literal[8]
    p: Literal[1]


class CipherDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["AES-256-GCM"]
    nonce: str


class AccountBundleEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["codex-lb-account-bundle"]
    version: Literal[1]
    kdf: ScryptDescriptor
    cipher: CipherDescriptor
    ciphertext: str


def new_payload(accounts: list[BundleAccount]) -> AccountBundlePayload:
    return AccountBundlePayload(
        created_at=datetime.now(timezone.utc),
        accounts=accounts,
    )


def encrypt_bundle(payload: AccountBundlePayload, passphrase: str, *, max_bytes: int) -> bytes:
    _validate_passphrase(passphrase)
    plaintext = _canonical_json(payload.model_dump(mode="json"))
    _check_size(plaintext, max_bytes)
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    metadata = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "kdf": {
            "name": "scrypt",
            "salt": _b64encode(salt),
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
        },
        "cipher": {"name": "AES-256-GCM", "nonce": _b64encode(nonce)},
    }
    aad = _canonical_json(metadata)
    ciphertext = AESGCM(_derive_key(passphrase, salt)).encrypt(nonce, plaintext, aad)
    encoded = _canonical_json({**metadata, "ciphertext": _b64encode(ciphertext)})
    _check_size(encoded, max_bytes)
    return encoded


def decrypt_bundle(raw: bytes, passphrase: str, *, max_bytes: int) -> AccountBundlePayload:
    _validate_passphrase(passphrase)
    _check_size(raw, max_bytes)
    try:
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise AccountBundleError("Invalid account bundle")
        if decoded.get("format") != BUNDLE_FORMAT or decoded.get("version") != BUNDLE_VERSION:
            raise UnsupportedAccountBundleError("Unsupported account bundle format")
        envelope = AccountBundleEnvelope.model_validate(decoded)
        salt = _b64decode(envelope.kdf.salt, expected_size=SALT_BYTES)
        nonce = _b64decode(envelope.cipher.nonce, expected_size=NONCE_BYTES)
        ciphertext = _b64decode(envelope.ciphertext)
        metadata = envelope.model_dump(mode="json", exclude={"ciphertext"})
        plaintext = AESGCM(_derive_key(passphrase, salt)).decrypt(
            nonce,
            ciphertext,
            _canonical_json(metadata),
        )
        _check_size(plaintext, max_bytes)
        payload = AccountBundlePayload.model_validate_json(plaintext)
    except UnsupportedAccountBundleError:
        raise
    except AccountBundleTooLargeError:
        raise
    except (InvalidTag, ValidationError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise AccountBundleError("Invalid account bundle or passphrase") from exc
    return payload


def bundle_integrity_token(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def mask_email(email: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator:
        return f"{email[:1]}***"
    return f"{local[:1]}***@{domain}"


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=KEY_BYTES, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(passphrase.encode())


def _validate_passphrase(passphrase: str) -> None:
    if not passphrase:
        raise AccountBundleError("A passphrase is required")


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str, *, expected_size: int | None = None) -> bytes:
    decoded = base64.b64decode(value, validate=True)
    if expected_size is not None and len(decoded) != expected_size:
        raise ValueError("invalid encoded field length")
    return decoded


def _check_size(value: bytes, max_bytes: int) -> None:
    if len(value) > max_bytes:
        raise AccountBundleTooLargeError("Account bundle exceeds the configured size limit")
