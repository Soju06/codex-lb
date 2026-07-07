"""Pydantic schemas for the Claude OAuth link flow endpoints.

Mirrors the conventions in ``app/modules/oauth/schemas.py`` and
``app/modules/claude/schemas.py``: ``DashboardModel`` base, camelCase JSON
aliases, ``min_length=1`` on operator-pasted values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.modules.claude.schemas import ClaudeAccountResponse
from app.modules.shared.schemas import DashboardModel


class ClaudeOauthStartResponse(DashboardModel):
    """Body of ``POST /api/claude/oauth/start``.

    Note: ``state_token`` is exposed to the authenticated dashboard session
    so the dialog can pre-fill the paste form. It is NOT a public secret;
    the dashboard session IS the trust boundary (the operator already had
    to authenticate to reach the dialog). ``GET /status`` continues to
    withhold it from any external caller.
    """

    flow_id: str = Field(min_length=1)
    authorization_url: str = Field(min_length=1)
    state_token: str = Field(min_length=1)
    expires_in_seconds: int = Field(gt=0)
    callback_instructions: str = Field(min_length=1)
    redirect_uri: str = Field(min_length=1)


class ClaudeOauthStatusResponse(DashboardModel):
    """Body of ``GET /api/claude/oauth/status``."""

    flow_id: str
    status: Literal["pending", "success", "error"]
    error_message: str | None = None
    error_code: str | None = None
    account_id: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class ClaudeOauthCallbackRequest(DashboardModel):
    """Body of ``POST /api/claude/oauth/callback``."""

    flow_id: str = Field(min_length=1)
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=4096)


class ClaudeOauthCallbackResponse(DashboardModel):
    """Body of ``POST /api/claude/oauth/callback`` on success."""

    status: Literal["success"]
    account: ClaudeAccountResponse
