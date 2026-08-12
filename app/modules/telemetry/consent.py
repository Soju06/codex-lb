from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Literal, cast
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.db.models import DashboardSettings
from app.modules.settings.repository import SettingsRepository

ConsentState = Literal["undecided", "enabled", "disabled"]
ConsentSource = Literal["env", "persisted", "default"]
_VALID_STATES = frozenset({"undecided", "enabled", "disabled"})


@dataclass(frozen=True, slots=True)
class ResolvedConsent:
    state: ConsentState
    source: ConsentSource
    active: bool


@dataclass(frozen=True, slots=True)
class TelemetryIdentity:
    instance_id: str
    private_key: Ed25519PrivateKey

    @property
    def public_key_hex(self) -> str:
        public_bytes = self.private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return public_bytes.hex()


def resolve_consent(telemetry_enabled: bool | None, persisted_state: str) -> ResolvedConsent:
    if telemetry_enabled is not None:
        state: ConsentState = "enabled" if telemetry_enabled else "disabled"
        return ResolvedConsent(state=state, source="env", active=telemetry_enabled)
    if persisted_state not in _VALID_STATES:
        raise ValueError(f"invalid telemetry consent state: {persisted_state}")
    state = cast("ConsentState", persisted_state)
    if state == "undecided":
        return ResolvedConsent(state="undecided", source="default", active=True)
    return ResolvedConsent(state=state, source="persisted", active=state == "enabled")


class TelemetryConsentStore:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        encryptor: TokenEncryptor | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._encryptor = encryptor or TokenEncryptor()
        self._repository = SettingsRepository(session)

    async def resolve(self) -> ResolvedConsent:
        row = await self._repository.get_or_create()
        return resolve_consent(self._settings.telemetry_enabled, row.telemetry_consent)

    async def set_decision(self, enabled: bool) -> ResolvedConsent:
        row = await self._repository.get_or_create()
        row.telemetry_consent = "enabled" if enabled else "disabled"
        await self._repository.commit_refresh(row)
        return resolve_consent(self._settings.telemetry_enabled, row.telemetry_consent)

    async def get_or_create_identity(self) -> TelemetryIdentity:
        row = await self._repository.get_or_create()
        if row.telemetry_instance_id is None or row.telemetry_private_key_encrypted is None:
            await self._mint_identity_if_missing()
            self._session.expire_all()
            row = await self._repository.get_or_create()
        if row.telemetry_instance_id is None or row.telemetry_private_key_encrypted is None:
            raise RuntimeError("telemetry identity could not be persisted")
        raw_private_key = base64.b64decode(self._encryptor.decrypt(row.telemetry_private_key_encrypted))
        private_key = Ed25519PrivateKey.from_private_bytes(raw_private_key)
        return TelemetryIdentity(instance_id=row.telemetry_instance_id, private_key=private_key)

    async def _mint_identity_if_missing(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        raw_private_key = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        encrypted = self._encryptor.encrypt(base64.b64encode(raw_private_key).decode("ascii"))
        await self._session.execute(
            update(DashboardSettings)
            .where(
                DashboardSettings.id == 1,
                or_(
                    DashboardSettings.telemetry_instance_id.is_(None),
                    DashboardSettings.telemetry_private_key_encrypted.is_(None),
                ),
            )
            .values(
                telemetry_instance_id=str(uuid4()),
                telemetry_private_key_encrypted=encrypted,
                version=DashboardSettings.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
