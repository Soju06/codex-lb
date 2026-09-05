# Preserve async tool continuity — change context

## Purpose / scope

Allow delayed async tool outputs to span intervening anchored turns
without the proxy synthesizing interrupted results.

## Decisions

- Split from #2089. This change is only async-tool continuity.
- The rust-v0.153.4 Codex binary and openai/codex tree do not emit
  `async: true` tools. This PR is protocol-forward.

## Constraints

- Sync interrupted-tool repair stays for non-async calls.
- Matching outputs complete only the corresponding pending async call.

## Failure modes

- Treating an async call as interrupted on the next turn would send a
  synthetic output and drop the real result later.
- Recording async calls into the durable pending-tool manifest would
  make a replica inject that synthetic output after failover.

## Example

Response 1 emits async `function_call` `call_a` and sync `call_b`.
Turn 2 is an anchored user message with no outputs: only `call_b` gets
a synthetic interrupted output. Turn 3 submits `call_a`'s real output
unchanged.

## Related

- Split from #2089. Slice (a): #2097.
