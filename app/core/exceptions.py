from __future__ import annotations

from datetime import datetime, timezone


class AppError(Exception):
    """Base exception for all domain errors."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "Unexpected error"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        self.message = message or self.__class__.message
        if code is not None:
            self.code = code
        super().__init__(self.message)


# --- OpenAI-envelope errors (proxy routes) ---


class ProxyAuthError(AppError):
    status_code = 401
    code = "invalid_api_key"
    error_type = "authentication_error"


class ProxyModelNotAllowed(AppError):
    status_code = 403
    code = "model_not_allowed"
    error_type = "permission_error"


class ProxyRateLimitError(AppError):
    status_code = 429
    code = "rate_limit_exceeded"
    error_type = "rate_limit_error"


class ProxyUpstreamError(AppError):
    status_code = 503
    code = "upstream_error"
    error_type = "server_error"


# --- Team mode (member gate) errors, OpenAI envelope ---


class TeamMemberSuspendedError(AppError):
    status_code = 403
    code = "team_member_suspended"
    error_type = "team_member_suspended"


class TeamModelNotAllowedError(AppError):
    status_code = 403
    code = "team_model_not_allowed"
    error_type = "team_model_not_allowed"


class TeamMemberOverCapError(AppError):
    status_code = 429
    code = "team_member_over_cap"
    error_type = "team_member_over_cap"

    def __init__(
        self,
        message: str,
        *,
        window: str,
        reset_at: datetime,
        code: str | None = None,
    ) -> None:
        self.window = window
        self.reset_at = reset_at
        super().__init__(message, code=code)

    @property
    def headers(self) -> dict[str, str]:
        # Window ends are computed in naive UTC, so stamp the zone before formatting;
        # without it the isoformat carries no offset and the client cannot tell.
        reset_at = self.reset_at if self.reset_at.tzinfo is not None else self.reset_at.replace(tzinfo=timezone.utc)
        return {
            "X-Team-Window": self.window,
            "X-Team-Reset": reset_at.isoformat().replace("+00:00", "Z"),
        }


# --- Dashboard-envelope errors ---


class DashboardAuthError(AppError):
    status_code = 401
    code = "authentication_required"


class DashboardForbiddenError(DashboardAuthError):
    """A dashboard caller that is authenticated enough, but not allowed from here.

    Subclasses ``DashboardAuthError`` so existing ``except`` clauses keep working, but
    answers 403 rather than 401 so the frontend does not treat it as a logged-out
    session and bounce the viewer to the login screen.
    """

    status_code = 403
    code = "forbidden"


class DashboardNotFoundError(AppError):
    status_code = 404
    code = "not_found"


class DashboardConflictError(AppError):
    status_code = 409
    code = "conflict"


class DashboardBadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class DashboardValidationError(AppError):
    status_code = 422
    code = "validation_error"


class DashboardRateLimitError(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int, code: str | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message, code=code)
