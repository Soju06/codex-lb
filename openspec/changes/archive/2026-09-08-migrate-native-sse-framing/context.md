# Native SSE framing migration

Python owns account/endpoint selection, replay eligibility, normalization,
terminal recognition, archives, persistence, and helper process lifecycle.
For a direct streaming Responses attempt, Rust owns HTTP body consumption,
SSE byte framing, byte limits, and the idle deadline between upstream body
reads. The existing request cancellation command owns teardown of that attempt.

For example, `data: {"type":"response.completed"}\n\n` split across network
reads becomes one IPC text event. Python processes its normal response lifecycle
without decoding base64 or scanning the SSE bytes again. A partial body read
resets the Rust idle deadline even when it contains no complete event. Downstream
backpressure does not become an upstream activity signal; the existing bounded
IPC queue still fails only its own request if its consumer falls behind.

IPC text fragments are capped at 16 KiB of UTF-8 and carry a `more` flag.
Python joins fragments to reconstruct each event, without byte decoding or
delimiter scanning. Fragmentation preserves the existing IPC line and queue
bounds even when JSON escaping or invalid UTF-8 replacement expands the text.
The reader yields after SSE fragments so a burst of small events from a single
network read can be consumed without immediately filling the shared reader's
per-request queue.

HTTP errors remain raw so status/error extraction sees the original body.
Non-streaming Responses, compact, routed HTTP, and WebSockets stay outside this
slice. An absent helper retains the existing pre-dispatch Python fallback;
an incompatible installed helper fails closed before any request. There is no
post-dispatch replay or independently active second framing owner.

Compatibility follows the existing Python byte parser, including CR/LF/CRLF,
mixed delimiters, immediately dispatching a trailing CR and swallowing its
following LF, UTF-8 replacement, non-ASCII line characters, whitespace-only
terminated blocks, and nonempty EOF residue. A complete oversized event and an
unterminated oversized buffer both fail; earlier valid events remain observable.
Transport-read chunk boundaries can affect trailing-CR representation and the
observed oversized-buffer length, as they already do in the Python transport.

The next migration should expand this framing contract to routed HTTP only
after its Codex response abstraction carries the same ownership explicitly.
Domain selection and persistence should be separate slices with characterization
fixtures; they do not belong inside the egress worker.
