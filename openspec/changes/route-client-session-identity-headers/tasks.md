# Tasks

## 1. Session identity recognition

- [x] 1.1 Add `x-session-affinity`, `x-session-id`, `x-opencode-session`, `x-claude-code-agent-id`, and `x-claude-remote-session-id` to the session-affinity header list, after the Codex names.
- [x] 1.2 Document the parent-header and request-id exclusions at the definition site.
- [x] 1.3 Recognize client-specific session identity on every Responses API entry point independently of the Codex-only affinity switch.
- [x] 1.4 Exclude client-specific identity aliases from non-Responses control-request affinity while preserving Codex-name routing.

## 2. Replay hygiene

- [x] 2.1 Strip the new headers in the account-neutral replay header filter.
- [x] 2.2 Strip client session identity at Responses upstream egress only; internal filters preserve the headers so request-log conversation metadata and session affinity still observe them, while non-Responses protocol metadata remains unchanged.
- [x] 2.3 Make generic inbound filtering preserve client identity by default; only Responses HTTP and WebSocket egress opt into stripping.
- [x] 2.4 Keep the shared HTTP header builder preserving identity by default; Responses and compact callers explicitly opt into stripping.

## 3. One-shot side-call bypass

- [x] 3.1 Bypass the bridge (raw HTTP upstream) for session-identified, tool-less, self-contained one-shots; exclude forwarded requests, native Codex clients, anonymous requests, and explicit `websocket` transport.
- [x] 3.2 Normalize the `tools: {}` wire shape (OpenCode title/compaction side calls) to `tools: []` on both `/responses` validation paths so empty tool maps reach the bypass; keep non-empty tool maps rejected.
- [x] 3.3 Keep account-scoped hosted input on the bridge and disable bare-session cap spillover for it.
- [x] 3.4 Keep stored-prompt requests on the bridge and disable bare-session cap spillover for them.
- [x] 3.5 Keep one-shot requests on the bridge when `auto` transport resolves under an effective `always_websocket` policy.
- [x] 3.6 Keep compact stored-prompt requests account-bound under account caps.

## 4. Tests

- [x] 4.1 Recognition, precedence, and exclusion coverage for the new headers.
- [x] 4.2 Account-neutral replay strip coverage for the new headers.
- [x] 4.3 One-shot predicate coverage: side calls bypass; tools, anchors, files, native Codex, forwarded, and anonymous requests keep the bridge.
- [x] 4.4 Empty tool map coverage: `tools: {}` validates as tool-less at the `/responses` route and reaches the one-shot bypass; non-empty tool maps stay rejected.
- [x] 4.5 Public Responses affinity coverage: client identity overrides shared prompt-cache affinity while Codex session affinity is disabled.
- [x] 4.6 Replica forwarding coverage: internal owner-forward headers retain client identity until Responses upstream egress.
- [x] 4.7 Account-scoped hosted-input coverage: such requests neither bypass the bridge nor spill across accounts.
- [x] 4.8 Stored-prompt coverage: such requests neither bypass the bridge nor spill across accounts.
- [x] 4.9 Always-websocket coverage: global and per-API-key policy keep one-shot requests on the bridge.
- [x] 4.10 Stored-prompt spillover coverage includes standard Responses and compact request models.
- [x] 4.11 Control-request coverage proves client-specific identity remains upstream metadata without supplying affinity.
