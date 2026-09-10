## Context

The existing model-source path already handles HTTP Responses forwarding, credentials and disabled-model denial. Catalog configuration is manual. CPA provides `GET /v1/models?client_version=...` under inference authentication; its ordinary list omits capability metadata.

## Goals / Non-Goals

Keep acquisition and reconciliation behind one model-source discovery interface. Reuse existing forwarding and native precedence. CPA remains independently operated. No provider login, translation engine, frontend, or live deployment is added.

## Decisions

Opt in through a persisted source catalog mode, defaulting to manual. This is a source topology choice, not a global environment setting. Acquire during catalog reads with a 60-second per-source refresh interval and one five-second acquisition budget across all sources. Store the last successful models. This avoids a second scheduled background lifecycle and makes discovery follow actual catalog use.

Fetch outside database transactions. Reconcile under a source configuration/concurrency fence. Validate the entire response before applying it. Require context metadata instead of inheriting the manual-source 128k fallback; enforce display-name and PostgreSQL integer bounds. An upstream template value is still a claim, even when structurally valid. Update existing rows in place and mark omissions disabled, preserving ownership and history. Existing disabled-source routing supplies an explicit error instead of native fallback.

Follow successful CPA list omissions for picker visibility and restore reappearing entries. This conservative, reversible assumption isolates the still-open suspension question; it does not infer permanent removal.

Translate only catalog fields needed by the established contract, preserving reported reasoning metadata while retaining HTTP transport. CPA template values are claims. Disposable fixtures prove protocol preservation; an exact CPA/provider pair is still needed to prove actual tool, context and reasoning capability.

## Risks / Trade-offs

- CPA may return a successful availability-filtered list. Retained disabled identities prevent native fallback and allow restoration.
- CPA may inherit model metadata. Do not claim provider compatibility from catalog success; retain exact proof limitations.
- Refresh can race configuration edits. Check the persisted configuration before writing and discard stale work.
- Catalog acquisition adds bounded latency when due. Cached data survives failed acquisition; native requests retain native routing.

## Migration Plan

Add source discovery configuration with manual defaults for existing rows. Rehearse migration on a disposable database. No runtime deployment belongs to this PR. Disable discovery to stop acquisition; cached source rows remain explicit operator-managed state.
