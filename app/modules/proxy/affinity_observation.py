from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.modules.proxy.affinity import _AffinityPolicy

# Length of the persisted key digest. Not comparable to the shape trace's
# ``prompt_cache_key`` digest: for a header-sourced policy the routing
# selection key is a different value.
_KEY_HASH_HEX_LENGTH = 16

# The complete domain of ``request_logs.sticky_key_source``.
AFFINITY_SOURCES: tuple[str, ...] = (
    "thread_header",
    "turn_state_header",
    "generated_turn_state",
    "session_header",
    "payload",
    "derived",
    "none",
)


@dataclass(frozen=True, slots=True)
class AffinityObservation:
    """Content-free snapshot carried by one request or logged attempt."""

    source: str
    kind: str | None
    key_hash: str | None

    @classmethod
    def from_policy(
        cls,
        policy: _AffinityPolicy,
        *,
        synthesized_turn_state: str | None = None,
    ) -> AffinityObservation:
        """Classify one resolved affinity policy.

        The single source of truth for every request-log path. Every input is
        read off the policy — ``codex_session_source`` and
        ``prompt_cache_key_source``, both recorded by the resolver — rather
        than re-sniffed from the headers or the payload. That matters because
        resolution mutates the payload (the derived cache key is written back
        onto it), so a caller re-deriving "did the payload carry a cache key"
        afterwards describes a request that was never sent.

        A policy carrying no key describes no decision, so a neutralized
        policy (``replace(policy, key=None, kind=None)``) reports ``none``.
        """
        key = policy.selection_key
        return cls(
            source=cls._classify(policy, synthesized_turn_state=synthesized_turn_state),
            kind=policy.kind.value if policy.kind is not None else None,
            key_hash=cls._hash(key),
        )

    @classmethod
    def retaining_source(cls, source: str, policy: _AffinityPolicy) -> AffinityObservation:
        """Re-project a policy while keeping the source already observed.

        Used only where a request is deliberately replayed with its affinity
        neutralized and the row should still say which signal the original
        attempt resolved.
        """
        return cls(
            source=source,
            kind=policy.kind.value if policy.kind is not None else None,
            key_hash=cls._hash(policy.selection_key),
        )

    @staticmethod
    def _hash(key: str | None) -> str | None:
        """Digest a routing key for persistence.

        Only the digest is ever stored: a *derived* key embeds a hash of
        prompt material, so the raw value stays behind the opt-in
        ``shape_raw_cache_key`` trace channel and never reaches a row.
        """
        if key is None:
            return None
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:_KEY_HASH_HEX_LENGTH]

    @staticmethod
    def _classify(policy: _AffinityPolicy, *, synthesized_turn_state: str | None) -> str:
        if policy.key is None:
            return "none"
        codex_session_source = policy.codex_session_source
        if codex_session_source == "thread_header":
            return "thread_header"
        if codex_session_source == "turn_state":
            if synthesized_turn_state is not None and policy.key == synthesized_turn_state:
                return "generated_turn_state"
            return "turn_state_header"
        if codex_session_source == "session_header":
            return "session_header"
        # The key is the prompt cache key; the resolver recorded whether the
        # client supplied it or the proxy derived it.
        return policy.prompt_cache_key_source or "derived"
