# Native capability routing context

## Purpose

Pooled Codex accounts must preserve Codex Responses semantics rather than
rotating accounts blindly. Model catalog visibility, account eligibility,
continuity ownership, service-tier intent, and actual upstream execution are
separate facts and must remain observable as such.

## Native semantics boundary

The Codex client continues to own the local harness: tool execution, sandbox,
approvals, skills, MCP, compaction decisions, and multi-agent workflow. The
proxy owns upstream account selection and transport compatibility. Changing
the selected account must not orphan a tool output, lose prior conversation
state, or send an account-scoped file reference to a different owner.

A `previous_response_id` is an upstream stored-object reference owned by the
account that created it. Therefore:

- a short continuation remains pinned to that owner;
- a verified full resend can drop the owner-scoped anchor and start fresh on a
  different eligible account;
- a full resend is not safe when it contains a tool output without its matching
  tool call, or when an account-scoped file reference still requires the old
  owner;
- no replay occurs after visible downstream output, because doing so could
  duplicate or fork the response.

This is why per-request round-robin routing is not equivalent to native Codex
continuity. Rotation is safe only at a fresh or reconstructable boundary.

## Model and Fast routing

The merged model catalog is a discovery union, not proof that every account can
serve every advertised model or tier. Selection uses the refreshed per-account
catalog and fails closed when no account advertises the requested capability.

`service_tier = "priority"` records Fast intent. It does not prove Fast was
granted. The completed response's actual `service_tier` remains authoritative;
`default` or `auto` means priority execution was not confirmed for that turn.

## Transport continuity

Direct WebSocket, HTTP-to-WebSocket bridge, and HTTP streaming/image bypass all
use the same replay-safety boundary. A successful HTTP-stream response records
its creator account so later owner lookup remains correct. If the upstream
anchor is unavailable across transports, a self-contained full resend may
recover without the anchor; a delta-only continuation fails closed.

The focused live validation sequence is:

1. WebSocket response that emits a function call.
2. HTTP image request containing the call and tool output as full resend.
3. WebSocket continuation containing self-contained history.
4. HTTP compact request.
5. WebSocket request consuming the compact output.

Success across this sequence demonstrates preserved tool and context semantics,
but it does not by itself prove that upstream granted Fast. Requested and actual
service tiers must still be checked separately.
