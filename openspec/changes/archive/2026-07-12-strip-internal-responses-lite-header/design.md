# Design

## Approach

Keep the exact lowercase `x-openai-internal-codex-responses-lite` header in the shared inbound upstream-header blocklist in `app/core/clients/proxy.py`. This prevents a stale or spoofed client header from enabling Lite mode on an incompatible model.

Before role-based instruction extraction, detect an input item whose `type` is `additional_tools` and leave the entire Lite input prefix unchanged. Codex deliberately places both the tool bundle and its base developer instructions in that prefix while leaving top-level `instructions` empty, so moving either item would diverge from the native wire shape.

Use the normalized body shape as the source of truth for Lite mode. A payload is Lite only when its input array contains an `additional_tools` item. Transport adaptation then reconstructs the official signal:

- HTTP Responses and compact requests receive `x-openai-internal-codex-responses-lite: true`.
- Websocket requests keep the handshake header blocked and receive `client_metadata.ws_request_header_x_openai_internal_codex_responses_lite = "true"` on each `response.create` frame.

The per-request websocket marker supports connections that switch between Lite and non-Lite models. When `additional_tools` is present, the marker is reconstructed or canonicalized to `"true"`. An incoming marker without the full prefix is accepted only when websocket continuity already observed `response.created` for a Lite request using the same effective upstream model after alias and API-key enforcement, and the frame's `previous_response_id` references the response ID recorded by that acceptance; a frame without `previous_response_id`, or referencing any other response, is stripped. A merely prepared request never changes this trusted state, and a non-Lite acceptance never clears it. An accepted `generate=false` prewarm does change it: Codex can reuse that response ID and send the first real request as an empty or user-only delta that no longer repeats `additional_tools`. Committing at upstream acceptance, before the response-create gate reopens, also makes pipelined turns follow request acceptance order rather than terminal-event order. The transparent fresh full-resend replay taken after an upstream previous-response miss clears `previous_response_id`, so a replay built from a trusted marker-only frame drops the marker rather than advertising Lite with neither prefix nor linkage; a replay whose own input contains `additional_tools` keeps it. The request state records which Lite model (if any) the fresh body advertises, and swapping to the fresh body swaps that value onto the acceptance flag, so a marker-stripped replay's `response.created` is not recorded as a Lite acceptance while a body-Lite replay still re-establishes trusted continuity. Acceptance records the downstream-visible response id: a suppressed-created replay keeps exposing the original id and rewrites every event to it, so continuity must trust that id, not the hidden upstream replay id the client never sees. This preserves legitimate Codex incremental frames without letting arbitrary client metadata or a policy change bypass the body-derived decision.

Compact requests may trim oversized conversation history before forwarding. The Lite tool bundle and its immediately following developer instructions are compact-state anchors: trimming may omit intervening history, but it cannot remove that native prefix or erase the body signal used to reconstruct the HTTP Lite header.

The HTTP-to-websocket bridge derives client metadata once from the normalized, untrimmed request and attaches that canonical mapping to a request-local payload copy. Prefix trimming, anchor injection, owner forwarding, and retry copies therefore retain the Lite marker even when the forwarded input delta no longer contains the original `additional_tools` item.

## Header Policy

Only the known unsupported inbound Lite header is blocked. The broader `x-openai-client-*` and `x-stainless-*` SDK fingerprint behavior stays unchanged. A canonical Lite signal is generated only after validating the matching body shape or trusted same-model websocket continuity, so incompatible requests retain the protection introduced by the blocklist.

## Failure Mode

Blindly forwarding the header can make an incompatible model fail before inference. Blindly stripping it while also rewriting the Lite input prefix makes a compatible model receive the wrong request shape or no tools. Deriving the signal from the preserved body avoids both failure modes.
