## ADDED Requirements

### Requirement: Subscription-overflow egress rewrites the request fields a third-party source must not receive as sent

A subscription-overflow dispatch SHALL NOT forward four request values to the designated model source as the client wrote them, and the rewrite SHALL happen on the egress body — the same projection that produces the body the portability verdict classifies, so that what was judged is what leaves. This requirement applies to the overflow direction **only**: direct source routing continues to forward `include`, `prompt_cache_key`, `user` and `safety_identifier` unchanged, as the telemetry-stripping requirement states.

The rewrites are exactly these:

- **`include`.** Every entry naming encrypted reasoning (`reasoning.encrypted_content`) MUST be removed, and `include` itself MUST be dropped once that removal leaves it empty. `include` requests extra *response* artifacts, so removing an entry can only subtract from what comes back and never changes the generation; the entry is present on 100 % of captured Codex 0.154.0 bodies, so declining on it instead would make overflow unreachable for every real Codex request; and the artifact it asks the destination to mint is the one this proxy rejects in every other position, so removing it reaches the end state the predicate already requires — no encrypted reasoning minted at the source, and a client's next turn that stays portable. Entries the removal does not name MUST be left in place.
- **`prompt_cache_key`, `user` and `safety_identifier`.** Each MUST be replaced with a proxy-minted opaque token: a deterministic function of the client's value, a namespace identifying the requesting API key, and a token domain that keeps the cache namespace and the end-user identity in separate token spaces. The token MUST be recognisable by shape alone, so the portability verdict can require the rewrite without being handed the namespace. A `prompt_cache_key` is a *namespace* at the destination and this proxy has measured that prompt caches are shared rather than isolated, so two tenants that chose the same value MUST NOT name one namespace at the source; `user` and `safety_identifier` are end-user identifiers by the OpenAI specification, in practice email addresses, and MUST NOT leave as written. The token MUST be stable for a given (API key, value) pair, so the source's prompt cache still survives across a tenant's turns and an end user remains one distinguishable subject.

A value in any of these slots that is not a string MUST be left exactly as it is rather than rewritten, so the projection never makes an unclassifiable value look classified; the portability verdict declines it.

#### Scenario: Codex's request for encrypted reasoning does not reach the source

- **WHEN** a Codex body carrying `include: ["reasoning.encrypted_content"]` is dispatched to the designated overflow source
- **THEN** the body the source receives carries no `include` field at all
- **WHEN** the same body carries `include: ["reasoning.encrypted_content", "message.output_text.logprobs"]`
- **THEN** the body the source receives carries `include: ["message.output_text.logprobs"]`
- **WHEN** the same body is forwarded by direct source routing instead
- **THEN** `include` is forwarded unchanged

#### Scenario: Two tenants that chose the same cache key do not share a namespace

- **GIVEN** two API keys whose requests both carry the same client-chosen `prompt_cache_key` and the same `user`
- **WHEN** both overflow to the same model source
- **THEN** neither the chosen cache key nor the end-user identifier appears anywhere in either forwarded body
- **AND** the two forwarded `prompt_cache_key` values differ from each other
- **AND** each is stable across that key's later turns with the same chosen value
- **AND** a request whose `user` and `safety_identifier` hold the same value forwards the same token for both

### Requirement: A portable verdict forwards no request field whose value this proxy has no answer for

The provider-portability verdict SHALL classify every top-level field the overflow portability view admits, and SHALL decline a body carrying a value in one of them that this proxy has no answer for. The classification MUST be a single closed table (`OVERFLOW_FIELD_CLASSIFICATION`) assigning each admitted field exactly one class — `validated` (a predicate reads the value and can decline it), `rewritten` (the value the client sent is not the value that leaves) or `forwarded` (carried as sent, with recorded evidence that it names no source-side state) — and the table MUST cover the view allowlist exactly, in both directions, so the field set stops being open on the value side the way it is already closed on the name side.

The field-value step MUST be evaluated after the configuration-class steps (Responses-Lite, tools, items, vision), so a body an operator can make portable by declaring a tool type keeps reporting that reason, and before the history step, because `not_portable_history` is the only reason that may earn the client a "start a new conversation" hint and a field value is not fixed by a new conversation. Its reason MUST be `not_portable_unknown_field`, with the offending field — and, for `include`, the offending entry — in `detail`. The step MUST NOT raise on any value: a non-list, a non-string element and a non-object map all answer here.

The rules are:

- `include` MUST be a closed allowlist of values whose named item class a portable body may itself carry. `message.input_image.image_url`, `message.output_text.logprobs` and `web_search_call.action.sources` are portable. `code_interpreter_call.outputs`, `computer_call_output.output.image_url` and `file_search_call.results` MUST decline, because they name hosted-tool artifacts whose declarations and items this module rejects everywhere else, so the response would carry an item the client then echoes into an unportable next turn. `reasoning.encrypted_content` MUST decline here too: the egress removes it, and this is what makes that removal load-bearing rather than advisory. Any other string, any non-string element and a non-list `include` MUST decline.
- `prompt_cache_key`, `user` and `safety_identifier` MUST be absent or hold a proxy-minted opaque token, so a bypassed egress rewrite fails closed instead of leaking.
- `metadata` MUST be absent or a map whose values are all strings and which names none of the account-scoped reference keys this module keys on (`file_id`, `container_id`, `vector_store_id`, `encrypted_content`, `file_ids`, `vector_store_ids`, a non-neutral `image_url`/`file_url`). It is otherwise forwarded: it is client-authored bookkeeping echoed back on the response object, strictly less exposing than the transcript it accompanies, and dropping it would silently break that echo.
- `prompt_cache_retention` MUST be absent or one of `in_memory` and `24h`; `truncation` MUST be absent or one of `auto` and `disabled`.
- `store` MUST NOT be declined or rewritten. It is the client's own storage intent, and the anchored-pin lifecycle exists precisely for a client that did not send `store: false` and expects a `previous_response_id` chain to resolve at the source.
- `instructions` is classified `forwarded` deliberately and MUST be recorded as such. The portability predicate is a check for source-side *state*, not a privacy check: in Codex 0.154.0 the environment context — absolute skill roots under the operator's `CODEX_HOME`, `sandbox_mode`, the approval policy — has moved out of `instructions` into the first developer message, which the request model folds back into `instructions`, and those same absolute paths already ride in the transcript's tool output and file contents. The decision that a designated source may read the operator's conversations is made once, when the operator designates it.

The fixture corpus gate MUST classify the body the overflow egress builds rather than the raw stripped body, so the recorded verdict is the one production computes, and MUST fail — naming the field — when a captured body carries a top-level field that production neither strips before the view, nor classifies in the table.

#### Scenario: An include value naming a hosted-tool artifact declines

- **WHEN** an otherwise portable body carries `include: ["file_search_call.results"]`
- **THEN** the verdict is `not_portable_unknown_field` with `include:file_search_call.results` in `detail`
- **WHEN** it carries `include: ["totally.made.up"]` or `include: [{"nested": "object"}]`
- **THEN** the verdict declines, naming the entry and the position respectively
- **WHEN** it carries `include: ["message.output_text.logprobs"]`
- **THEN** the verdict is `portable`

#### Scenario: An end-user identifier the egress did not rewrite fails closed

- **WHEN** an otherwise portable body reaches the verdict carrying `user: "operator@example.com"`
- **THEN** the verdict is `not_portable_unknown_field` naming `user`
- **WHEN** the same body carries the proxy-minted token the egress produces
- **THEN** the verdict is `portable`

#### Scenario: metadata is forwarded unless it names a reference

- **WHEN** an otherwise portable body carries `metadata: {"team": "a"}`
- **THEN** the verdict is `portable`
- **WHEN** it carries `metadata: {"file_id": "file_abc"}` or a metadata value that is not a string
- **THEN** the verdict is `not_portable_unknown_field` naming `metadata`

#### Scenario: The field-value step never displaces a reason the operator can act on

- **WHEN** a body declares a tool type the source model has not declared *and* carries an unrewritten identifier
- **THEN** the verdict is `not_portable_tools`, naming the tool type
- **WHEN** a body carries a `previous_response_id` and every field value is one this proxy has an answer for
- **THEN** the verdict is `not_portable_history`

#### Scenario: A captured body carrying an unclassified field fails the corpus gate

- **WHEN** a committed fixture carries a top-level field that production neither strips before the view nor classifies
- **THEN** the corpus gate fails and names that field
