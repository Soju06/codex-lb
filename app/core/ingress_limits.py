"""Fixed HTTP ingress body budgets (issue #1340 / PRINCIPLES.md P2).

Leaf module shared by the request-body guard middleware and the CLI so the
downstream websocket ceiling (``--ws-max-size``) stays in lockstep with the
Responses HTTP budget instead of drifting as two hard-coded numbers.
"""

from __future__ import annotations

from typing import Final

# General raw and decompressed request-body budget for every guarded HTTP path.
MAX_DECOMPRESSED_BODY_BYTES: Final[int] = 32 * 1024 * 1024
# Responses ingress (``/v1/responses``, ``/backend-api/codex/responses``): Codex
# clients resend the whole conversation history (inline screenshots included) in
# one request after a reconnect, so this budget is deliberately larger.
MAX_DECOMPRESSED_RESPONSES_BODY_BYTES: Final[int] = 128 * 1024 * 1024
