# Change Proposal

Codex sessions can receive replayed `response.output_item.done` tool-call events with a new `call_id` but the same side-effecting operation. Passing both copies through causes duplicate local shell writes, terminal polls, or patch applications.

## Changes

- Suppress duplicate same-response downstream tool-call events for local side-effect tools.
- Treat `exec_command`, `write_stdin`, and `apply_patch_call` as side-effecting operations where `call_id` changes do not make a replay safe.
- Rewrite `multi_tool_use.parallel` arguments before forwarding so duplicate nested side-effect operations are removed from a single batch.
- Keep distinct read-only calls and calls under later response ids visible.
