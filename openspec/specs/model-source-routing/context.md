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

## Model aliases

Operators can give opaque custom models a stable public ID. In Model Sources, create or edit the Models field using `cd/gpt-6-astra=cd/linxaq`. Entries are separated by commas or newlines; a bare model ID keeps identity forwarding. To retain both public names, list both `cd/linxaq` and `cd/gpt-6-astra=cd/linxaq` in the same source. Both entries use that source's credential; aliases do not create a token pool.

The left side is the model clients request and select in their catalog. The right side must exactly match the ID accepted by the upstream endpoint. For an endpoint whose actual ID is `ch/linxaq`, the corresponding entry is `cd/gpt-6-astra=ch/linxaq`.

The dashboard API represents the same mapping as:

```json
{
  "model": "cd/gpt-6-astra",
  "rawMetadataJson": "{\"upstream_model\":\"cd/linxaq\"}"
}
```

Merge `upstream_model` into existing raw metadata; replacing the entire object can discard capabilities such as `multi_agent_version`, instructions and reasoning settings. Editing an existing model into `new-alias=existing-model-id` in the dashboard carries its settings across automatically. Clearing `=upstream-id` removes the mapping. API updates replace the supplied model list, so callers must retain every model they intend to keep.

The routing setting lives in the existing metadata JSON to avoid a database migration or environment setting. Resolution happens after the proxy checks the public model's permissions, capability and source assignment. Targets are one-hop strings, not links to other model entries. Pricing and logs remain keyed by the public model. Only protocol model fields in successful JSON/SSE responses are translated; generated text, function arguments and upstream error messages are unchanged.

Roll out support to every replica before saving alias metadata. Older versions ignore this setting and would send the public alias directly upstream. Remove mappings before reverting to an older version. Existing sources without the setting keep their current behavior. A client with a pinned local model catalog must refresh that file after aliases are configured, then select the public ID.

The feature uses the existing HTTP routes; it adds no WebSocket or compaction support. Invalid targets are rejected at create/update time. Empty sides, duplicate public names and multiple equals separators are rejected by the form. Unsupported trailing-slash URLs retain their existing errors.


## Multiple credentials for a Codex model

Responses requests automatically share enabled model sources declaring the same public model. Each source still contains one upstream API key. For example, retain `Code Hole Remote (admin-pc)` and add four sources with distinct names, the same endpoint and model declarations, and one new upstream key per source. Preserve the original alias, reasoning, tools and agent metadata in each copy. An API key restricted to source assignments must include the sources intended for its pool. Clients continue using one codex-lb endpoint/key and the same public model such as `cd/gpt-6-astra`.

The dispatcher prefers fewer active Responses requests and rotates equally loaded sources. A saturated source is skipped according to its existing `maxConcurrency`. Equally new candidates are chosen randomly to avoid every replica starting with the same source. This applies to `/v1/responses` and `/backend-api/codex/responses`, including their trailing-slash variants. Chat Completions, Embeddings and Audio retain their existing selection; subscription account routing settings do not control this source pool. No extra toggle or environment setting is required.

Fresh requests retain declared collaboration namespace tools and neutral controls such as `background: false`, `max_tool_calls` and `stream_options.include_obfuscation` when a second source is added. Each source needs the same tools/agent capability metadata. Accepting these declarations does not make opaque upstream state portable or relax subscription-overflow compatibility rules.

Before returning a stream or completed JSON, a portable request without credential-bound history may try up to five different sources after an explicit upstream 401/403, 429 or 5xx rejection, or a failure known to occur during connection establishment. Each attempt owns its own admission slot and usage reservation. Cleanup and logging complete before cooldown changes and the next reservation. Exhausted attempts return the final source's redacted error and Retry-After. No replay occurs after the stream is handed to the client, for ambiguous header/body timeouts, malformed successful bodies, client disconnection or failed reservation release.

For pools, default cooldowns are 300 seconds for rejected credentials, 60 seconds for HTTP 429 and 5 seconds for server/connect failures. Numeric or HTTP-date Retry-After overrides the default, bounded to 1–600 seconds. Replacing the source key or endpoint clears its old cooldown, including updates within the same database timestamp second. Sources become eligible again after cooldown; a failed probe cools them again. A pool with no available source returns 503 `model_source_busy` with Retry-After before reserving quota. Single-source operation keeps its previous admission/error behavior.

Counters, rotation and cooldowns are local to each worker/replica and capped at 10,000 cached source states. Restarting a worker clears that transient state. `maxConcurrency` remains a per-worker limit; it is not a global HA limit, and cooldowns are not a persistent dashboard health status. Request logs identify the actual source of every dispatched attempt and remain the diagnostic surface.

### Continuation ownership and failure modes

Responses ownership is shared in the database and scoped by the exact codex-lb API-key identity and public model. The proxy records response IDs, output item IDs, call IDs and encrypted-content fingerprints before exposing their event frames or JSON result, including item IDs first seen in normalized text/tool deltas. Unresolved tool outputs are bound through their `call_id`; a complete ordered call/result pair remains portable when the call and output types match and it has no opaque upstream state. Returned bookkeeping item IDs on a complete pair do not change its portability; the forwarding body is preserved. Matching call IDs alone are insufficient. A continuation can therefore arrive on a different backend immediately after `response.completed`, even while accounting logs are still being finalized. Only SHA-256 reference fingerprints are stored; raw encrypted content is not retained in ownership records. Active records expire after 30 days without refresh and the existing retention worker purges them. Compact historical source/revision evidence remains after expiry and pruning, so expiration cannot authorize a different credential. PostgreSQL deletion rechecks expiry after waiting on another transaction so a concurrent refresh on another backend is preserved. Historical evidence currently has no automatic retention limit.

`previous_response_id`, `conversation`, item references and encrypted reasoning bind a request to its recorded source. Multiple references must agree. Removed, disabled, disallowed, busy or cooling owners never cause a switch; this also applies when no matching source remains and the ordinary source lookup misses, including public names that request normalization would otherwise rewrite. Changing a recorded owner's credential, endpoint or upstream-model mapping invalidates its transport fingerprint. Saving the identical token preserves its existing encrypted bytes and continuity. Each continuity reference introduced by source request overrides must be resolved before contacting upstream, including in single-source configurations; a known owner of one response does not authorize another unknown response ID. Each source candidate is checked independently: an override on source B cannot block a valid continuation through source A. Unknown/conflicting state in a pool returns 409 (`previous_response_owner_unavailable` for response anchors, otherwise `model_source_owner_unavailable`). Lookup failure returns 502 before dispatch; publishing failure withholds the successful output, returns `model_source_ownership_unavailable` and completes reservation/admission cleanup without failover. A cancelled request with captured upstream usage retains the existing settlement policy.

For example, `gpt-5-high` may normalize to `gpt-5` after its own source disappears. A response created under `gpt-5-high` remains evidence in that original public-model scope, so the `gpt-5` source cannot receive its anchor. A response originally served by the `gpt-5` fallback is recorded under `gpt-5` and can still continue there. The same rule applies when the original source is disabled or loses streaming capability, and it remains isolated by client API key. If both names happen to refer to one source row, losing the original model mapping still prevents assuming that the fallback transport has the original response's revision.

MCP approval responses use the returned approval request item's ID as their ownership reference. An anchor from source A combined with an approval item from B fails before dispatch; an anchor and approval item from A continue on A. Object-form conversation IDs in source request overrides are resolved like response conversation IDs. A malformed object such as `{ "conversation": { "id": 42 } }` is rejected before dispatch; a valid source's request can still proceed when another pool member has malformed override metadata. Original client-supplied external conversation state remains compatible with one source when no ownership conflict is known.

Source overrides can also replace input after the route's initial file check. The final source payload is checked for `input_file` and `input_image` file IDs, so an operator override cannot pass a file uploaded to a subscription account to a model source. The overridden request is declined before a source reservation; it is not redirected to an account chosen from an earlier version of the body. Original client file references still take the subscription route.

If an override gives an input item or nested content/output part a nonstring `type`, that source is ineligible before file-ID extraction. Other valid sources remain usable. This protects the shared candidate lookup from one malformed metadata value without changing how the original client request is validated.

Code-interpreter output can expose a container ID that a later request names in `tools[].container` or a retained input item. These IDs are recorded and checked within the same client-key/public-model scope as response and item IDs. `{"type":"auto"}` has no container reference; its existing single-source acceptance and hosted-tool pool portability rules remain unchanged. A source model that explicitly supports a custom `namespace` tool or `web_search` can receive a fresh declaration with a nonblank namespace name or a boolean `external_web_access` option. The source receives the original tool JSON. These direct-source allowances do not change the narrower subscription-overflow replay check.

File-search declarations can likewise name upstream vector stores in `vector_store_ids`. A successful single-source request using an external vector store records that ID, and subsequent declarations resolve it within the client-key/public-model scope. The check reads only those hosted-tool fields; function parameter schemas are data definitions and are not treated as references.

An empty `stream_options` object is a neutral direct-source control and can accompany a fresh pooled request; it remains in the forwarded JSON. If an upstream JSON response was generated but its ID collides with existing source ownership, the client receives a 502 ownership error and no output. The request log still records that upstream's 200 status, usage and timings for diagnosis, while the reservation is released without charging the client or trying another credential.

Durable ownership is authoritative; failed competing publications in accounting logs cannot displace it. Log-only response IDs resolve only when their recorded source and credential revision are unambiguous and still match. Logs written before credential revisions were recorded cannot prove that the current token owns the old response: those continuations and attempted publications that collide with them fail closed, including in a single-source configuration. Send portable context without the old reference in that case. Externally created state with no conflicting ownership evidence retains single-source compatibility; successful requests record that accepted state for later pool use. Changing the client key or public alias changes the ownership scope. Full context that still contains encrypted or other account-bound state is not portable merely because `previous_response_id` is absent. File/subscription-owned requests retain their existing path.

Responses redirects are not followed. Any 3xx produces 502 `model_source_redirect` without another upstream attempt or a client-visible Location. Configure the source's final Responses base URL. Chat Completions, Embeddings and Audio keep their previous redirect policy.

### HA rollout

Use the existing HA surge deployment for blue/green/amber and their shared PostgreSQL database. The history migration is additive: it copies both live and expired ownership revisions into historical evidence and adds a nullable revision to request logs. Existing log revisions remain unknown; the migration does not infer them from current tokens. Finish upgrading every backend before adding multiple eligible sources for a model. Older binaries do not publish all required evidence or enforce the new routing checks. If a model already has multiple sources, keep each affected client key assigned to its known source through the mixed-version window; avoid replacing its credential during that window. Old log-only continuations may be rejected by upgraded backends because their credential cannot be verified. Local cooldown/load counters remain per worker; only durable continuity is shared. Rolling application rollback does not imply downgrading the shared schema.

See [spec.md](spec.md) for the routing and dashboard contracts.

### Prompt ownership and source request compatibility

Prompt template IDs use the same durable client-key/public-model scope as other source references. A prompt learned during a successful single-source request remains bound to that source after more credentials are assigned, including when the next request lands on another backend. For example, a response anchor from A combined with `prompt: {"id": "pmpt_B"}` learned on B is rejected; one known anchor cannot authorize another unknown reference. Changing the owner's credential also rejects that prompt after active ownership records expire, because historical credential evidence remains.

Earlier releases did not publish prompt IDs. A prompt with no recorded owner is therefore still denied in a pool; a successful request with just its intended source assigned can establish ownership. This uses the existing fingerprint tables and requires no migration. Apply the fix to all production backends through the existing HA rollout when deploying; old backends do not enforce the new prompt guard.

Direct-source requests reject `input_file` or `input_image` file references in `prompt.variables`, including `sediment://` image references, before reserving source quota. This covers both client variables and metadata overrides, even if the prompt and response anchor otherwise belong to the source. It does not redirect override-supplied files to a subscription account or add subscription prompt-template support. Text, inline file data, and ordinary image URLs retain field-preserving forwarding. The established subscription route for files in original request input is unchanged.

Original source reference checks use the body retained for source forwarding before overrides. Subscription cleanup must not erase a compacted-history reference from that comparison. In particular, an old local compact fallback marker followed by an external compaction item remains acceptable with one source when ownership does not conflict; known conflicting or unavailable owners still fail closed.

Valid integer `top_logprobs` values from 0 to 20 are neutral direct-source controls. Adding a second source or retrying a portable request after an explicit upstream rejection preserves the value and `include: ["message.output_text.logprobs"]`, including SDK streaming. Booleans and malformed values do not gain portability through this allowance, and subscription-overflow replay rules are unchanged.

## Codex client messages when expanding a source pool

Codex attaches locally generated `msg_...` IDs to inline user and instruction messages. A message carrying all of its own content is different from an upstream output reference. Direct-source selection and portability share a strict classifier for user/system/developer messages: require an absent type or `type: message`, remove only `id` in a classification copy, then validate the remaining message using the existing account-neutral contract. The forwarded request still carries the ID. Assistant output IDs, encrypted reasoning, compaction, file references and malformed messages retain ownership checks. A developer-role `additional_tools` bundle is not a message; any ID it carries remains ownership evidence.

For example, an owned encrypted reasoning item followed by a new user message with its own ID continues on the recorded credential after four other sources are enabled, including on a different replica. A fresh self-contained message can use the pool. An unknown encrypted item still cannot safely choose among credentials. Disabling the real owner does not authorize another token.

Declared direct-source web search also accepts validated `search_content_types: ["text", "image"]`, which Codex sends alongside `external_web_access`. Both controls are retained on the wire. These allowances do not change subscription-account replay classification or introduce new settings.

The motivating production 409 was not body-archived. Its one-source-versus-five-source behavior was independently reproduced using a synthetic request captured from installed Codex CLI 0.157.1 with the source catalog; the reporting client used Codex Desktop 0.158.0-alpha.2.1. Re-enabling production sources is a separate operational step after deploying the change, not part of local verification.

## Client-generated tool-result IDs

Codex can attach a new local `id` to a `function_call_output` while its `call_id` names a call already emitted by the model. This is especially visible with retained namespaced calls: the original call/result group does not qualify for the existing complete-pair shortcut, even when source forwarding removes the call namespace. Requiring the upstream to have previously emitted the client's result ID caused an old conversation to fail with 409.

Direct-source ownership now treats only the local result ID on a strictly validated function/custom/apply-patch result as bookkeeping. For example, `{"type":"function_call_output","id":"local-result","call_id":"owned-call","output":"done"}` continues on the recorded owner of `owned-call`, including on another backend. The forwarded result retains its ID and content. A result-only continuation remains bound through its call ID; a response anchor cannot authorize an unknown call. Opaque state, file-backed content, unknown fields and unsupported result kinds retain conservative checks. Upstream output publication and subscription replay are unchanged.

The September 27 retry diagnostic found nineteen references with one current source owner and one unknown client result ID whose call ID had that same owner. It did not show mixed encrypted owners, so changing session affinity or discarding reasoning was not necessary for this failure. Deploy the correction to all HA backends; no source reconfiguration or schema migration is needed. See [spec.md](spec.md) for the contract.

## Standalone Codex subtask notifications

Codex can inject a named `function_call_output` with no `call_id`, for example
`{"type":"function_call_output","id":"fco_local","name":"send_message_to_thread","namespace":"codex_app","output":"Delegated task context"}`.
This is a self-contained notification, not a result referring to an upstream call.
The Codex protocol explicitly tests named unpaired output serialization in
[models.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs).
The affected configured endpoint accepts this shape; strict providers can still reject
it as described in [upstream issue 45914](https://github.com/openai/codex/issues/45914).

Direct-source routing validates the complete known shape before excluding it from
ownership and portability classification. The forwarding body stays unchanged to
preserve the notification, ordering and cache prefix. A present `call_id` (including
null or blank), unknown fields, opaque state or account-owned file content does not
qualify. Existing response/call ownership still pins continuations across replicas;
subscription replay remains stricter. This does not create a synthetic call or
convert tool output into system/developer instructions, and it does not guarantee
compatibility with every third-party Responses implementation.

## Inline Codex agent messages

The inline agent-message requirement in [spec.md](spec.md) distinguishes client-authored inter-agent task envelopes from retained upstream state. Codex v2 constructs an `agent_message` with a local ID, author, recipient and inline text/encrypted content. Requiring prior ownership of that local ID blocks new subtasks when a model has several source credentials. Synthetic testing confirmed that the configured custom endpoint could read the same encrypted task generated by a subscription parent under all five source credentials. This is not evidence that reasoning or compaction state can cross accounts.

For example, `{"type":"agent_message","id":"local-task","author":"/root","recipient":"/root/worker","content":[{"type":"encrypted_content","encrypted_content":"<task>"}]}` is complete inline task content. The proxy forwards it unchanged and excludes it only from the direct-source ownership/portability calculation. A sibling `previous_response_id`, retained reasoning, compaction or tool result continues to require its ordinary owner. The local ID used later as a bare `item_reference` also requires ownership.

Only the validated envelope and supported content parts qualify. Extra fields, file references and malformed content do not gain portability; subscription replay remains unchanged. A custom upstream still needs to understand this Responses item. Deployment requires no source, credential or database changes and follows the HA surge rollout. See [Codex's protocol types](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs) and [OpenAI's agent-message description](https://developers.openai.com/api/docs/guides/responses-multi-agent#new-multi-agent-output-items).
