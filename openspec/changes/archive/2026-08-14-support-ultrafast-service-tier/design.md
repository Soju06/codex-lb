## Context

The Responses request models, upstream transports, request logs, and model registry already carry service tiers as normalized strings. They therefore preserve `ultrafast` without a transport change and can route it using live per-account catalog metadata. The remaining hard-coded allowlists are the API-key CRUD contract and dashboard controls.

OpenAI documents `ultrafast` as an access-controlled processing tier currently available for `gpt-5.6-sol`. Entitlement must therefore come from each account's live upstream catalog instead of a static plan or bootstrap assumption.

## Goals / Non-Goals

**Goals:**

- Make `ultrafast` a supported canonical API-key service tier.
- Expose the tier through the existing dashboard API-key controls.
- Preserve existing entitlement-aware account routing and response-tier logging.
- Add focused regression coverage and user-facing compatibility notes.

**Non-Goals:**

- Invent an `ultrafast` model-name alias.
- Advertise Ultrafast from bootstrap metadata or grant it to a plan statically.
- Add a setting, dependency, or database migration.
- Guess a distinct Ultrafast token price that OpenAI has not published.

## Decisions

1. Add `ultrafast` only to the existing backend and frontend API-key tier allowlists. The request models and transports already pass it through, so adding another normalization layer would duplicate working behavior.
2. Keep `ultrafast` canonical. Unlike the legacy `fast` alias, it is an upstream wire value and must not normalize to `priority`.
3. Reuse live model-catalog routing. An explicit or enforced Ultrafast request can select only accounts whose catalog advertises that tier; the existing enforced-tier fallback still removes it for models that do not advertise it.
4. Do not add Ultrafast to the bundled model catalog. Static metadata cannot prove access to an access-controlled preview and would expose a tier that an imported account may not hold.
5. Keep pricing unchanged. No distinct public Ultrafast token price is available in the official OpenAI documentation, so this change does not introduce a speculative multiplier.

## Risks / Trade-offs

- [An entitled account's catalog does not advertise `ultrafast`] → The existing explicit-tier routing error remains visible instead of silently selecting an ineligible account.
- [OpenAI later publishes distinct Ultrafast pricing] → Add the published rates in a focused pricing change before claiming separate cost accuracy.
- [Dashboard-visible option requires review evidence] → Include before and after screenshots in the PR body as required by the simplicity gates.
