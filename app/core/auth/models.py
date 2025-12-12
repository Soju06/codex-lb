from __future__ import annotations


from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictStr, field_validator

from app.core.types import JsonObject


class OAuthTokenPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: StrictStr | None = None
    refresh_token: StrictStr | None = None
    id_token: StrictStr | None = None
    authorization_code: StrictStr | None = None
    code_verifier: StrictStr | None = None
    error: JsonObject | StrictStr | None = None
    error_description: StrictStr | None = None
    message: StrictStr | None = None
    error_code: StrictStr | None = None
    code: StrictStr | None = None
    status: StrictStr | None = None


class DeviceCodePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    device_auth_id: StrictStr | None = None
    user_code: StrictStr | None = Field(
        default=None,
        validation_alias=AliasChoices("user_code", "usercode"),
    )
    interval: int | None = None
    expires_in: int | None = None
    expires_at: StrictStr | None = None

    @field_validator("interval", mode="before")
    @classmethod
    def _parse_interval(cls, value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            if stripped.isdigit():
                return int(stripped)
        raise ValueError("Invalid interval")
