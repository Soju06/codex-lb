# Model Source Routing — Context

## Purpose

Capability-based routing and accounting for OpenAI-compatible model sources,
including field-preserving embeddings forwarding.

This capability keeps source selection separate from subscription-account
routing: embeddings traffic is served only by sources that declare the
embeddings capability, while Responses/chat/audio continue to use their own
capability gates. Field presence (including explicit nulls) is preserved on
embeddings forwards so compatible sources see the same payload shape the
client sent.


## Public model aliases

Operators may enter `public-model=provider-model` in the model-source form. The public ID governs selection, permissions and accounting; the selected source resolves its upstream target only at forwarding. Structured response model fields return to the public ID without rewriting generated text or tool arguments. A missing mapping preserves the existing identity behavior.
