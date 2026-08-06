# Context: developer-interleaved fresh resends

## Why the transparency conditions are shaped this way

The guard exists so that a verified durable Responses-Lite prefix containing a Codex
`developer` message can still be proven replay-safe. Every condition is fail-closed, so
this document records what real resend payloads actually look like and which conditions
they meet.

## Measured corpus

2,798 real `POST /backend-api/codex/responses` request bodies captured from Codex CLI
traffic against a codex-lb deployment. Every request was Responses-Lite
(`input[0].type == "additional_tools"`), `store: false`, `previous_response_id: null`, so
every non-first turn is a full cumulative resend of the whole history. Consecutive
same-thread requests were confirmed byte-identical on their shared prefix.

## The observed traffic does not reach these allowances

Running the production classification path over the corpus — the ID-preserving projection
followed by the stored-prefix walk — the prefix proof fails for **0 of 2,798 payloads
passing / 2,798 failing**. Two independent gates sit upstream of every developer-specific
condition:

| Gate | Payloads blocked | Cause |
| --- | --- | --- |
| Lite tool bundle is not account-neutral | 2,763 / 2,798 | The bundle declares `namespace` (2,763 payloads) and `tool_search` (1,621) tool types, which are outside `_ACCOUNT_NEUTRAL_TOOL_TYPES` (`custom`, `function`, `web_search`, `web_search_preview`). `_is_canonical_lite_tool_bundle` is therefore false, `canonical_lite_developer_index` is never set, and the canonical developer message at original index 1 is rejected. |
| More than one stored developer message | the remaining 35 | Real prefixes carry several developer messages (permissions, agent instructions, mode banners). Only one canonical slot exists, so every additional developer message sits with no pending call and fails closed. |

So the motivating reconnect shape is **not currently reachable in production**, and the
fixture correction alone does not change that. Both gates are deliberate fail-closed
behaviour that this change does not widen; widening either one is a separate decision with
its own evidence and review.

## Developer-message shapes observed on the wire

Recorded because they determine which conditions could ever be satisfied, even though the
gates above bind first.

- **Response-owned IDs.** 7,666 of 16,036 inline developer items carry an `id`. Among
  unique occurrences of the motivating interleave (a completed `custom_tool_call`, a
  developer message, then that call's matching output), 40 are ID-less and 55 carry an
  `id`. Those IDs are client-minted (`msg_` + a dashed UUIDv7 whose timestamp matches the
  originating thread) and are textually distinct from response-owned IDs (`msg_`/`rs_`/
  `ctc_` + dash-free hex), but replay classification does not model ID provenance: any
  `id` is treated as possibly response-owned and fails closed. Because production always
  classifies through the ID-preserving projection, fixtures for the accepted shape must be
  ID-less and must be classified with `preserve_developer_message_ids=True`; otherwise they
  assert acceptance of an input production would refuse.
- **No turn metadata.** 0 of 16,036 inline developer items carry
  `internal_chat_message_metadata_passthrough`. Observed key sets are exactly
  `{content, role, type}` (8,370) and `{content, id, role, type}` (7,666). This binds
  asymmetrically: the historical path tolerates absent metadata because
  `_internal_chat_message_metadata_is_account_neutral(None)` is `True`, while both
  fresh-developer paths require `isinstance(metadata, dict)` plus an exact `turn_id` and so
  can never admit a real inline developer message.

## Bounding the historical interleave

Accepting a transparent developer message consumes nothing, so an unbounded rule would
admit shapes never observed and never needed: a parallel batch
(`call1 -> call2 -> developer -> output1 -> output2`), the same batch with the developer
moved before the second call (`call1 -> developer -> call2 -> output1 -> output2`), and a
duplicate (`call -> developer -> developer -> output`).

The window rule is: a pending window opens when the pending-call deque becomes non-empty
and closes when it drains; a developer message is transparent only in a window that has
never held more than one outstanding call and has not already consumed a developer
message; and once a window has consumed its developer message it must not become parallel.
Sequential single-call windows each keep their one interleaved message.

The canonical Lite-prefix developer message is proven separately, and the two allowances
cannot interact: the canonical position is original index 1, whose only predecessor is the
`additional_tools` bundle, so it is always outside any pending window.

No parallel batch appeared anywhere in the measured corpus, so the rule that reopens
transparency for a clean window after a parallel window drains is a conservative
allowance rather than an observed requirement. It is covered by a positive regression so
the width stays a decision rather than an accident.

## Known widths this change does not close

- Classification runs on the **projected** input, which omits `reasoning` and completed
  bookkeeping items. The two fresh-developer allowances therefore measure suffix
  exactness and terminality against projected positions, so a real
  `reasoning -> call -> developer -> output` suffix presents as the exact three-item shape,
  and a real `final_answer -> user -> developer -> reasoning` suffix presents as terminal.
  Closing this needs original-position evidence threaded into those checks, which pairs
  naturally with replacing the bare `canonical_lite_developer_index` int with the
  projection object.
- Call-like items outside the supported direct tool-call vocabulary (for example
  `computer_call`) are tolerated in the prefix without opening a pending window, so they do
  not count toward the one-outstanding-call bound. Pre-existing, and unchanged here.
