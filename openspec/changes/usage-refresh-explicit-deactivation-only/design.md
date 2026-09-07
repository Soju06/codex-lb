## Context

See [proposal.md](proposal.md) for the incident and motivation. Usage refresh currently classifies HTTP 402 and 404 as terminal before its ordinary non-deactivating error path. Both the initial fetch and the post-refresh retry call the same classifier, so the correction must live in that shared classifier.

The usage client preserves an upstream envelope's nested `error.code` and `error.message`. Repository tests contain a concrete `account_deactivated` envelope with the message `Your OpenAI account has been deactivated.` and the balancer's existing permanent-failure registry defines the status mapping for credential/session and disabled-account codes. Searches of application code, tests, active specs, and published docs found no concrete usage-response envelope for account/subscription not-found, unpaid, or payment-required terminal states.

## Goals / Non-Goals

**Goals:**

- Make persistent account-status changes depend only on explicit error content.
- Preserve the existing permanent-code status mapping and explicit deactivation-message fallback.
- Send ambiguous failures through the existing non-deactivating refresh-failure path on both fetch attempts.

**Non-Goals:**

- Automatically reactivate accounts already deactivated by prior failures.
- Add fleet-wide outage correlation or new settings.
- Change proxy request-path handling of upstream 404 responses.
- Infer new terminal codes or messages without recorded upstream evidence.

## Decisions

### Remove HTTP status from the terminal classifier

The shared usage-error classifier will inspect only recognized permanent-failure codes and existing explicit deactivation-message hints. The status-code allowlist will be removed. This automatically applies the same rule to the initial attempt and post-auth-refresh retry and reuses the existing non-deactivating path used by 403, including current logging/error accounting and retry eligibility.

Alternative considered: retain 402 or 404 behind message/body checks tailored to account-not-found or payment-required responses. This was rejected because the repository has no concrete upstream envelope showing those conditions are account-specific and terminal.

### Keep status mapping centralized

Recognized codes continue through the balancer's existing permanent-failure registry and account-status mapper. Explicit deactivation-message hints continue to select `deactivated` when no recognized code exists. No second code registry or response-shape parser will be introduced in the updater.

Alternative considered: duplicate specific terminal codes in the updater. This was rejected because it would create a second source of truth and could drift from proxy/auth failure handling.

## Risks / Trade-offs

- [A truly terminal upstream response exposes only a bare status] → The account remains eligible and refresh retries continue; this is intentionally safer than permanently removing healthy capacity on an ambiguous signal.
- [Upstream introduces a new explicit terminal envelope] → Add support only after capturing concrete evidence and updating the policy and regression tests.
- [Existing deactivated rows remain unavailable] → Operators must reactivate them through the existing endpoint; automatic repair is explicitly outside this change.

## Migration Plan

Deploy as a code-only policy correction with no data migration. Rollback restores the prior classifier but would reintroduce outage-driven fleet deactivation; already persisted statuses are unchanged in either direction.
