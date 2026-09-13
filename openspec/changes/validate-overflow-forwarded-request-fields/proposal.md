## Why

The subscription-exhaustion overflow (issue #2123) hands a Responses body to a
**third-party** OpenAI-compatible provider. `replay_safety` decides whether the
body is *portable*, and the view allowlist decides which top-level field **names**
may reach that provider. Nothing decided what the **values** in six of those
fields say once they arrive, so a `portable` verdict forwarded them unread.

A capture sweep of 60 real `codex-cli 0.154.0` bodies through the exact
fixtures-gate path (`/mnt/scratch/tmp/codex-body-sweep-0913/REPORT.md`, section 6)
reproduced five of these on a body the predicate called PORTABLE:

1. **`include` was never validated.** `include: ["reasoning.encrypted_content"]`
   is on **all 60** captured bodies and passed. So did
   `["file_search_call.results", "message.input_image.image_url"]`,
   `["totally.made.up"]` and `[{"nested": "object"}]` — a non-string element did
   not even fail closed. The asymmetry is the finding: `encrypted_content`
   sitting *in* a transcript is rejected in two places, while the request asking
   the destination to **mint** it sailed through. Scope, stated precisely: the
   request model already closes the `include` *vocabulary* to seven values and
   to `list[str]`, so the unknown and non-string cases are 400s before the
   overflow path is reached and the rules for them are backstops for a
   hand-built view. What was live in production is the seven-value vocabulary
   itself — `reasoning.encrypted_content` on every Codex request, and three
   values naming hosted-tool artifacts this module rejects everywhere else.
2. **`prompt_cache_key` was forwarded verbatim** and never validated
   (`strip_source_telemetry`'s docstring says so explicitly). A cache key is a
   *namespace* at the destination; a value one tenant chooses and replayed to a
   source another tenant shares is the cross-tenant cache-namespace collision
   class — the same class the 2026-09-11 cross-account cache probe measured
   upstream, where the prompt cache was **not** isolated between accounts.
3. **`metadata`, `user`, `safety_identifier` and `prompt_cache_retention` were
   never validated.** `user: "operator@example.com"` yielded PORTABLE.
   `_PORTABILITY_VIEW_ONLY_FIELDS` described the group as "provider-neutral
   generation knobs with no account-scoped state", which is false for two
   end-user identifiers. Latent for Codex today, live for any SDK client.
4. **Operator filesystem paths reach the third party.** In 0.154.0 the
   environment context is no longer in `instructions`: it moved into the first
   developer message, carrying absolute skill roots under the real `CODEX_HOME`,
   `sandbox_mode` and the approval policy — which the request model then folds
   back into `instructions`.
5. **`store: true` passed.** "The destination may persist this transcript" was a
   decision the overflow path did not make.

The reason this drifted is that the predicate was written against imagined
shapes and gated against two hand-written bodies.

## What Changes

- **The forwarded field set stops being open on the value side.** A new closed
  table (`replay_safety.OVERFLOW_FIELD_CLASSIFICATION`) gives every field the
  view admits exactly one class — `validated`, `rewritten` or `forwarded` — and
  a new verdict step declines any field whose value is not one this proxy has an
  answer for, with `not_portable_unknown_field` naming the field. The step sits
  after the configuration-class reasons (an operator keeps the reason they can
  act on) and before `not_portable_history` (only that reason earns the client a
  "start a new conversation" hint, and a field value is not fixed by one).
- **Rule per leak, argued from the evidence rather than from symmetry:**
  - **`include`: neutralise `reasoning.encrypted_content`, decline the rest.**
    Declining on it would make overflow unreachable for 100 % of real Codex
    traffic; removing it is free, because `include` only adds fields to the
    *response*, and it reaches the end state the predicate already wants — no
    encrypted reasoning minted at the source, so the client's next turn stays
    portable. Every other value is a closed allowlist: portable exactly when the
    item class it names is one a portable body may carry, which excludes the
    three hosted-tool artifacts and everything unknown or non-string.
  - **`prompt_cache_key`, `user`, `safety_identifier`: neutralise (replace).**
    Each becomes a proxy-minted opaque token, a pure function of the client
    value, the requesting API key and a token domain. The cache still hits
    across a tenant's turns and an end user is still one distinguishable
    subject, while two tenants that chose the same value can no longer name one
    namespace and no email leaves. Declining would kill the feature for Codex
    (60/60 carry a cache key); dropping would cost the source its abuse tooling.
  - **`metadata`: declare safe, with a shape rule.** Client-authored bookkeeping
    echoed back on the response, strictly less exposing than the transcript it
    accompanies — dropping it would silently break that echo. What it may not be
    is a *reference*: it must be the API's own `Map<string, string>` and must not
    name one of the account-scoped reference keys this module already keys on.
  - **`prompt_cache_retention`: declare safe, with a closed enum.** The client's
    own retention choice about its own data at a source the operator designated.
  - **`store`: declare safe, no new rejection.** Not symmetry — the anchored-pin
    lifecycle exists *precisely* for a client that did not send `store: false`
    and expects a `previous_response_id` chain to resolve at the source.
    Declining or forcing it would break a feature the design already ships.
  - **`instructions` (the operator's paths): declare, out of scope, recorded.**
    The predicate is a state check, not a privacy check. The same absolute paths
    already ride in the transcript's tool output and file contents, so a rule
    that redacts `instructions` alone is theatre; and stripping the environment
    context changes how the model behaves. The decision that a designated source
    may read the operator's conversations is made at configuration time.
- **The neutralisation happens on the overflow egress**, in one function both
  the body the verdict classifies (`overflow._source_body`) and the body the
  route helper puts on the wire go through, so what was judged is what leaves.
  The verdict then *requires* the rewritten shape, so a bypassed rewrite fails
  closed instead of leaking. Direct source routing is untouched: it still
  forwards `include`, `prompt_cache_key`, `user` and `safety_identifier` exactly
  as the client sent them.
- **The predicate is grounded in the real corpus.** The fixture gate now
  classifies the body the overflow egress builds (what production classifies),
  and a drift guard fails — naming the field — when a captured body carries a
  top-level field production neither strips, nor rewrites, nor validates, nor
  has recorded evidence for.
- **Three factual corrections** the sweep found in the corpus docs: the real
  `tool_search` carries **two** fields beyond the allowlist (`execution` **and**
  `parameters`); the `web_search` extras are profile-dependent (standard slugs
  send `external_web_access` + `search_content_types`, the fallback profile sends
  only `external_web_access`); the `additional_tools` bundle item carries an `id`
  outside its neutral field set. The README divergence table described the
  pre-0.154.0 `instructions` layout. All four field sets are now asserted against
  the committed bytes instead of only described.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: the telemetry-stripping requirement carves out the
  overflow direction for the four rewritten fields the way it already carves out
  `service_tier`; the provider-portability requirement gains the closed
  field-value step and the classification table.

## Impact

- Edited: `app/modules/model_sources/projection.py` (egress neutralisation),
  `app/modules/proxy/replay_safety.py` (classification table + field-value step),
  `app/modules/proxy/overflow.py` (`overflow_source_wire_body`),
  `app/modules/proxy/api.py` (one call on the overflow egress),
  `tests/fixtures/codex_bodies/{README.md,provenance.json}`,
  `tests/unit/test_{replay_safety_portability,model_sources_projection,codex_body_fixtures,subscription_overflow_store_intent}.py`,
  `tests/integration/test_subscription_overflow_routing.py`.
- No Alembic revision, no settings, no `CODEX_LB_*` variable, no dashboard or
  API surface, no frontend, no new fixture body.
- Behaviour change on the overflow direction only. Threads pinned before this
  change keep hitting the source's prompt cache under a different key once: the
  forwarded key changes shape, so the first turn after deploy is a cache miss.
