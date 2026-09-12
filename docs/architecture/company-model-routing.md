# Company model routing and token accounting

This page explains how subscription-backed GPT models and company model
providers coexist behind one codex-lb endpoint. Normative routing behavior is
owned by [model-source-routing](../../openspec/specs/model-source-routing/spec.md).

## System context

```mermaid
C4Context
  title codex-lb model resource context

  Person(user, "Codex user", "Chooses a model for each task")
  System(codex, "Codex client", "Sends Responses requests")
  System(lb, "Local codex-lb", "Routes by exact model identity")
  System_Ext(openai, "OpenAI Codex service", "Consumes one bound GPT account quota")
  System_Ext(trae, "TRAE gateway", "Consumes the local company TRAE entitlement")
  System_Ext(codebase, "Codebase LLMProxy", "Consumes the local Codebase company entitlement")
  System_Ext(llmbox, "LLMBox", "Consumes the local LLMBox entitlement")

  Rel(user, codex, "Selects a model in")
  Rel(codex, lb, "Sends model + prompt + tools", "OpenAI Responses/HTTP")
  Rel(lb, openai, "Routes subscription models using an account")
  Rel(lb, trae, "Routes trae/* models using local login")
  Rel(lb, codebase, "Routes codebase/* models using local login")
  Rel(lb, llmbox, "Routes fixed LLMBox models using local login")
```

## Containers and resource ownership

```mermaid
C4Container
  title codex-lb routing containers

  Container(codex, "Codex", "Desktop/CLI", "Chooses an exact model ID")

  System_Boundary(lb, "Local codex-lb") {
    Container(catalog, "Unified model catalog", "Model registry", "Combines subscription and enabled source models")
    Container(router, "Model router", "FastAPI", "Resolves exact model ownership")
    Container(accounts, "GPT account scheduler", "Account pool", "Chooses among bound GPT accounts")
    Container(adapters, "Company provider adapters", "Responses/SSE bridges", "Uses the selected provider directly")
    ContainerDb(store, "Usage store", "SQLite", "Records request and token observations")
    Container(dashboard, "Dashboard", "React", "Shows account quota and source observations")
  }

  Container_Ext(openai, "OpenAI", "Codex backend", "Owns per-account quota windows")
  Container_Ext(company, "Company gateways", "TRAE / Codebase / LLMBox", "Own company-side entitlements and limits")

  Rel(codex, catalog, "Reads available models", "HTTP")
  Rel(codex, router, "Requests selected model", "Responses API")
  Rel(router, accounts, "Delegates subscription model")
  Rel(accounts, openai, "Authenticates as selected GPT account", "HTTPS")
  Rel(router, adapters, "Delegates company-owned model")
  Rel(adapters, company, "Authenticates with local company login", "HTTPS/SSE")
  Rel(router, store, "Records routed source and tokens", "SQL")
  Rel(dashboard, store, "Reads quota snapshots and observations", "SQL")
```

## Request flow

```mermaid
C4Dynamic
  title One Codex request through codex-lb

  Container(codex, "Codex", "Desktop/CLI", "Sends the chosen model ID")
  Container(router, "Model router", "codex-lb", "Resolves exact ownership")
  Container(accounts, "GPT account scheduler", "codex-lb", "Selects an eligible account")
  Container(adapters, "Company adapter", "codex-lb", "Translates the selected provider protocol")
  ContainerDb(logs, "Request log", "SQLite", "Stores observed token usage")
  Container_Ext(openai, "OpenAI", "Subscription quota")
  Container_Ext(company, "Company provider", "Company entitlement")

  Rel(codex, router, "1. Send exact model ID")
  Rel(router, accounts, "2a. Subscription model: request an account")
  Rel(accounts, openai, "3a. Consume selected GPT account quota")
  Rel(router, adapters, "2b. Company model: choose source directly")
  Rel(adapters, company, "3b. Consume company provider entitlement")
  Rel(router, logs, "4. Record route + reported input/output tokens")
```

The two branches do not share quota. Examples:

| Selected model | codex-lb route | Credential/resource consumed |
| --- | --- | --- |
| `gpt-5.4` | GPT account scheduler | One eligible bound OpenAI account |
| `trae/GPT-6-Astra` | TRAE adapter | Local TRAE company login |
| `codebase/kimi-k2.6` | Codebase Chat adapter | Local Codebase-backed TRAE login |
| `codebase/gpt-5.4` | Codebase Responses adapter | Local Codebase-backed TRAE login |
| `deepseek-v4-flash-0731` | LLMBox adapter | Local LLMBox login |

For bound OpenAI accounts, codex-lb can display upstream quota windows and use
them during account selection. The company gateways currently expose no
validated remaining-quota contract. Their dashboard cards therefore show
remaining quota and reset time as `unknown`, while separately showing the
input/output tokens reported by completed requests during the retained 24-hour
window. These observations measure traffic; they are not a company quota
balance.

Provider errors do not silently move a company model request into the GPT
account pool. Source namespacing (`trae/*` and `codebase/*`) makes ownership
explicit and prevents same-name GPT models from colliding with subscription
models.

## Company operational controls

The production implementation adds a company admission layer before forwarding:
observed health and cooldown, then a rolling 24-hour local token budget, then
the existing Responses concurrency control. A two-hour scheduler probes each
enabled company model and hides models whose last probe failed, exceeded the
latency threshold, or became stale. A 429 or credential failure triggers a
60-second cooldown, as do three consecutive qualifying server/transport
failures. Client cancellations and ordinary validation errors do not make the
source unhealthy. After cooldown, the next request can establish recovery; a
successful response restores healthy status.

The optional local budget defaults to unlimited. It rejects new requests once
reported input plus output tokens reach the configured limit. This is a soft
limit: missing upstream usage and concurrent in-flight requests can exceed it.
The dashboard exposes these limitations alongside successes, errors and latency.
The contract is part of [model-source-routing](../../openspec/specs/model-source-routing/spec.md); production evidence is in [company source governance verification](../../openspec/changes/archive/2026-09-10-add-company-source-governance/verification.md).

Local macOS upgrades can use `scripts/local_launchagent_cutover.py`. The
one-shot harness rejects execution as a KeepAlive LaunchAgent, validates the
candidate executable before stopping the current service, allows a bounded
startup window, and restores the saved plist when bootstrap or health checks
fail. Its unit tests cover delayed startup, bootstrap failure, health timeout,
rollback verification, and the KeepAlive regression.
