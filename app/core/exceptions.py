from __future__ import annotations


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


# --- Dashboard-envelope errors ---


class DashboardAuthError(AppError):
    status_code = 401
    code = "authentication_required"


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
