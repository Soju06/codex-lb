## Purpose and evidence

Codex can request `include: ["reasoning.encrypted_content", "output.encrypted_content"]` even for a plain text turn. Rejecting the latter value prevents dispatch before the model sees the request.

The [Codex client at 33d306d](https://github.com/openai/codex/blob/33d306d457a4632b22bebf214e0c25d91f90f6e8/codex-rs/core/src/client.rs#L1130) adds the output selector when output token replay is enabled for its OpenAI provider. Its [ResponseItem model](https://github.com/openai/codex/blob/33d306d457a4632b22bebf214e0c25d91f90f6e8/codex-rs/protocol/src/models.rs#L1052) carries ciphertext on messages and tool calls as well as reasoning.

The proxy treats these values as opaque. For example, a message with `id: "msg_1"`, `phase: "final_answer"` and synthetic `encrypted_content: "opaque+/="` is returned with those fields and retains them when included in the next request's input. The selector is additive, not an alias for reasoning encryption.

No transport, account-ownership, compaction, authentication or composer-prediction policy changes are part of this fix. Synthetic regression data establishes forwarding and rejection behavior, not the validity or decryptability of real ciphertext.
