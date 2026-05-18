from __future__ import annotations


class ClientPayloadError(ValueError):
    """Validation error that should be surfaced to the client as a 400.

    ``code`` and ``error_type`` allow callers to mirror specific OpenAI
    error shapes (for example ``invalid_json_schema`` /
    ``invalid_request_error`` for strict-mode schema violations) instead
    of the generic ``invalid_request_error`` payload used by default.
    """

    def __init__(
        self,
        message: str,
        *,
        param: str | None = None,
        code: str | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.param = param
        self.code = code
        self.error_type = error_type
