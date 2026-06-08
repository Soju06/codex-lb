from __future__ import annotations

from pydantic import Field

from app.core.types import JsonValue
from app.modules.shared.schemas import DashboardModel


class AntigravityHarnessPrintRequest(DashboardModel):
    prompt: str = Field(min_length=1, max_length=20000)
    workspace_path: str = Field(min_length=1, max_length=4096)
    timeout_seconds: int = Field(default=300, ge=1, le=1800)
    add_dirs: list[str] = Field(default_factory=list, max_length=16)
    conversation_id: str | None = Field(default=None, max_length=255)
    continue_conversation: bool = False
    sandbox: str | None = Field(default=None, max_length=120)


class AntigravityHarnessPrintResponse(DashboardModel):
    provider_id: str
    account_id: str
    external_account_id: str | None = None
    command: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class AntigravityManagedInteractionRunRequest(DashboardModel):
    agent: str = Field(default="antigravity-preview-05-2026", min_length=1, max_length=255)
    input: str = Field(min_length=1, max_length=20000)
    environment: str = Field(default="remote", min_length=1, max_length=255)
    tools: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=16)


class AntigravityManagedInteractionRunResponse(DashboardModel):
    provider_id: str
    agent: str
    output_text: str
    response: dict[str, JsonValue]
