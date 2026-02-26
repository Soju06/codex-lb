from __future__ import annotations

from dataclasses import dataclass

from app.modules.settings.repository import SettingsRepository


@dataclass(frozen=True, slots=True)
class DashboardSettingsData:
    sticky_threads_enabled: bool
    prefer_earlier_reset_accounts: bool
    routing_strategy: str
    global_model_force_enabled: bool
    global_model_force_model: str | None
    global_model_force_reasoning_effort: str | None
    import_without_overwrite: bool
    totp_required_on_login: bool
    totp_configured: bool
    api_key_auth_enabled: bool


@dataclass(frozen=True, slots=True)
class DashboardSettingsUpdateData:
    sticky_threads_enabled: bool
    prefer_earlier_reset_accounts: bool
    routing_strategy: str
    global_model_force_enabled: bool
    global_model_force_model: str | None
    global_model_force_reasoning_effort: str | None
    import_without_overwrite: bool
    totp_required_on_login: bool
    api_key_auth_enabled: bool


class SettingsService:
    def __init__(self, repository: SettingsRepository) -> None:
        self._repository = repository

    async def get_settings(self) -> DashboardSettingsData:
        row = await self._repository.get_or_create()
        return DashboardSettingsData(
            sticky_threads_enabled=row.sticky_threads_enabled,
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            routing_strategy=row.routing_strategy,
            global_model_force_enabled=bool(getattr(row, "global_model_force_enabled", False)),
            global_model_force_model=getattr(row, "global_model_force_model", None),
            global_model_force_reasoning_effort=getattr(row, "global_model_force_reasoning_effort", None),
            import_without_overwrite=row.import_without_overwrite,
            totp_required_on_login=row.totp_required_on_login,
            totp_configured=row.totp_secret_encrypted is not None,
            api_key_auth_enabled=row.api_key_auth_enabled,
        )

    async def update_settings(self, payload: DashboardSettingsUpdateData) -> DashboardSettingsData:
        current = await self._repository.get_or_create()
        if payload.totp_required_on_login and current.totp_secret_encrypted is None:
            raise ValueError("Configure TOTP before enabling login enforcement")

        model = _normalize_optional(payload.global_model_force_model)
        effort = _normalize_force_reasoning_effort(payload.global_model_force_reasoning_effort)
        if payload.global_model_force_enabled and model is None:
            raise ValueError("Select a model before enabling force-all routing")

        row = await self._repository.update(
            sticky_threads_enabled=payload.sticky_threads_enabled,
            prefer_earlier_reset_accounts=payload.prefer_earlier_reset_accounts,
            routing_strategy=payload.routing_strategy,
            global_model_force_enabled=payload.global_model_force_enabled,
            global_model_force_model=model,
            global_model_force_reasoning_effort=effort,
            import_without_overwrite=payload.import_without_overwrite,
            totp_required_on_login=payload.totp_required_on_login,
            api_key_auth_enabled=payload.api_key_auth_enabled,
        )
        return DashboardSettingsData(
            sticky_threads_enabled=row.sticky_threads_enabled,
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            routing_strategy=row.routing_strategy,
            global_model_force_enabled=bool(getattr(row, "global_model_force_enabled", False)),
            global_model_force_model=getattr(row, "global_model_force_model", None),
            global_model_force_reasoning_effort=getattr(row, "global_model_force_reasoning_effort", None),
            import_without_overwrite=row.import_without_overwrite,
            totp_required_on_login=row.totp_required_on_login,
            totp_configured=row.totp_secret_encrypted is not None,
            api_key_auth_enabled=row.api_key_auth_enabled,
        )


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_force_reasoning_effort(value: str | None) -> str | None:
    normalized = _normalize_optional(value)
    if normalized is None:
        return None
    lowered = normalized.lower()
    if lowered == "medium":
        return "normal"
    if lowered not in {"low", "normal", "high", "xhigh"}:
        raise ValueError("Invalid forced reasoning effort")
    return lowered
