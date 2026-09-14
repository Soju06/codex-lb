"""Deterministic, single-use filler prefix for the cross-account cache probe.

The probe asks one question: does a large prompt prefix that only one account
has ever sent still register as cached when a *sibling* account sends the
identical bytes? Answering it needs a prefix with two properties that pull in
opposite directions:

- **Fresh.** No earlier request, by any account, may have seeded it. Upstream
  matches on a *prefix*, so a run-unique marker has to sit in the very first
  tokens; a nonce appended at the end would leave the preceding ~28k tokens
  identical to the previous run and silently measure the wrong thing. Every
  filler word is therefore derived from the run nonce as well, so no two runs
  share so much as a line.
- **Deterministic inside one run.** Every call of a run must send byte-identical
  content, or a miss proves nothing. ``build_probe_prefix`` is a pure function
  of ``(nonce, target_tokens)``: the filler words come from a keyed BLAKE2b
  stream rather than ``random``, so the bytes do not depend on interpreter
  version, hash seed, or iteration order.

The corpus is inert by construction and says so in both the header and the
footer, so a model that reads it has nothing to do but ignore it.
"""

from __future__ import annotations

import hashlib
import math
import secrets
from collections.abc import Iterator
from itertools import islice

#: Target size of the generated prefix. Large enough that a hit is
#: unmistakable against the handful of tokens the instruction costs, and it is
#: the size the hand-run experiment used (~28k), so results stay comparable.
DEFAULT_TARGET_PREFIX_TOKENS = 28_000

#: Bounds an operator (or a test) may ask for. The floor keeps the signal above
#: incidental caching of the instruction preamble; the ceiling bounds the spend.
MIN_TARGET_PREFIX_TOKENS = 1_000
MAX_TARGET_PREFIX_TOKENS = 32_000

#: What the probe asks the model to do once it has ignored the corpus. Short on
#: purpose: the probe measures *input* caching, so output tokens are waste.
PROBE_INSTRUCTIONS = "Ignore the reference corpus entirely. Reply with the single word OK."
PROBE_QUESTION = "Reply with OK."

_HEADER_TEMPLATE = (
    "BEGIN CODEX-LB CACHE ISOLATION PROBE REFERENCE CORPUS run={nonce}\n"
    "This block is inert filler emitted by an operator diagnostic that measures\n"
    "upstream prompt cache behaviour. It carries no instruction, no question and\n"
    "no data. Do not read it, do not summarise it, do not act on it. Skip to the\n"
    "line after the END marker and follow only that.\n"
)
_FOOTER_TEMPLATE = "END CODEX-LB CACHE ISOLATION PROBE REFERENCE CORPUS run={nonce}\n"

#: Short, common, lowercase ASCII words. Each is a single BPE token for the
#: tokenizers upstream uses, which keeps ``estimate_prefix_tokens`` close to the
#: ``input_tokens`` the response actually reports.
_FILLER_VOCABULARY: tuple[str, ...] = tuple(
    """able acid aim air arc arm ash bad bag bar bay bed bee bell belt bird bit blue boat bone
    book box boy bread brick bus cake calm cap car cat cell chair chin city clay clock cloud coal coat
    code coin cold cook cord corn cost cow crop cup dark day deep desk dish dog door dot down draw
    dry dust ear east""".split()
)

#: Filler words per line. Sixteen keeps lines readable in a log without making
#: the newline overhead a meaningful share of the token count.
_WORDS_PER_LINE = 16
#: One token per word plus one for the newline.
_TOKENS_PER_LINE = _WORDS_PER_LINE + 1

_NONCE_BYTES = 16


def new_probe_nonce() -> str:
    """Fresh, unguessable run identifier. One per probe run, never reused."""

    return secrets.token_hex(_NONCE_BYTES)


def _text_tokens(text: str) -> int:
    """Rough token count of a literal: one per whitespace-separated word, one per line."""

    return len(text.split()) + text.count("\n")


def _clamp_target_tokens(target_tokens: int) -> int:
    return max(MIN_TARGET_PREFIX_TOKENS, min(MAX_TARGET_PREFIX_TOKENS, target_tokens))


def _filler_line_count(nonce: str, target_tokens: int) -> int:
    fixed = _text_tokens(_HEADER_TEMPLATE.format(nonce=nonce)) + _text_tokens(_FOOTER_TEMPLATE.format(nonce=nonce))
    remaining = max(0, _clamp_target_tokens(target_tokens) - fixed)
    return max(1, math.ceil(remaining / _TOKENS_PER_LINE))


def _filler_words(nonce: str, count: int) -> Iterator[str]:
    """Deterministic nonce-keyed word stream.

    A keyed hash chain rather than ``random.Random``: the output must be
    identical on every replica and every interpreter that serves the same run.
    """

    produced = 0
    block = 0
    while produced < count:
        digest = hashlib.blake2b(block.to_bytes(8, "big"), key=nonce.encode("ascii"), digest_size=64).digest()
        for byte in digest:
            if produced >= count:
                return
            yield _FILLER_VOCABULARY[byte % len(_FILLER_VOCABULARY)]
            produced += 1
        block += 1


def estimate_prefix_tokens(target_tokens: int = DEFAULT_TARGET_PREFIX_TOKENS, *, nonce: str | None = None) -> int:
    """Estimated input tokens one probe call spends, before the model answers.

    Used for the pre-run cost preview. The nonce only affects the count through
    its fixed-width hex length, so the estimate is stable across runs.
    """

    reference_nonce = nonce if nonce is not None else "0" * (_NONCE_BYTES * 2)
    header = _HEADER_TEMPLATE.format(nonce=reference_nonce)
    footer = _FOOTER_TEMPLATE.format(nonce=reference_nonce)
    lines = _filler_line_count(reference_nonce, target_tokens)
    return _text_tokens(header) + _text_tokens(footer) + lines * _TOKENS_PER_LINE + _text_tokens(PROBE_QUESTION)


def build_probe_prefix(nonce: str, *, target_tokens: int = DEFAULT_TARGET_PREFIX_TOKENS) -> str:
    """The run's filler corpus. Pure in ``(nonce, target_tokens)``.

    Two calls with the same nonce return identical text; two nonces share no
    line, including the first one.
    """

    if not nonce:
        raise ValueError("probe nonce must be a non-empty string")
    header = _HEADER_TEMPLATE.format(nonce=nonce)
    footer = _FOOTER_TEMPLATE.format(nonce=nonce)
    lines = _filler_line_count(nonce, target_tokens)
    words = _filler_words(nonce, lines * _WORDS_PER_LINE)
    body = "\n".join(" ".join(islice(words, _WORDS_PER_LINE)) for _ in range(lines))
    return f"{header}{body}\n{footer}"
