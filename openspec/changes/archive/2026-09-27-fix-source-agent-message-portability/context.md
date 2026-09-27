## Incident and protocol evidence

Codex v2 constructs `agent_message` input locally from an inter-agent communication. Its ID belongs to the client message envelope; it is not necessarily an ID returned by the selected source. Content can contain `input_text` and `encrypted_content` parts. This distinction matters when a subscription parent delegates to a custom model with several source credentials.

A synthetic subscription call generated an encrypted task requesting a fixed verification marker. The same generated task was sent directly to each of the five configured custom-source credentials: all five returned HTTP 200 and the correct marker. Sending the same shape through the production Codex route reproduced `model_source_owner_unavailable` before dispatch. These observations establish the reported false rejection; they do not establish that arbitrary encrypted reasoning or compaction can move between accounts.

Protocol references: [Codex inter-agent communication implementation](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs), [agent-message content types](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs), and [OpenAI multi-agent output items](https://developers.openai.com/api/docs/guides/responses-multi-agent#new-multi-agent-output-items).

## Example

```json
{"type":"agent_message","id":"client-local-task","author":"/root","recipient":"/root/probe","content":[{"type":"input_text","text":"Message Type: NEW_TASK\nPayload:\n"},{"type":"encrypted_content","encrypted_content":"<inline encrypted task>"}]}
```

The proxy sends this envelope unchanged. For source classification it is inline content. Adding `previous_response_id`, a reasoning/compaction item, or an unsettled call still invokes the ordinary ownership checks. Submitting `client-local-task` as an `item_reference` also invokes them.

## Limits and operations

The exemption is limited to the validated agent-message envelope, not arbitrary dictionaries carrying ciphertext. Unexpected content fields or file references remain outside it. File inputs continue through the existing subscription file-owner route. Unsupported upstreams can reject the forwarded item with their normal protocol response; this change does not make every OpenAI-compatible provider support Codex agent messages.

No credentials, raw user messages, or captured ciphertext were written to diagnostics. Production capture used only shapes and scoped fingerprints. The rollout uses the existing HA surge script, with no changes to source definitions or ownership tables.
