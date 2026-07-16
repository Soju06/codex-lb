# Design: fix-replica-deployment-artifacts

## Decisions

### 1. NetworkPolicy fix is overlay values + NOTES warning, not a template default

The synced `deployment-networking` spec requires fail-closed rendering (no `namespaceSelector: {}` when the allowlist is empty) — that requirement stays intact.

- **Rejected**: auto-rendering an allow-all or defaulting `ingressNSMatchLabels` in `values.yaml` (would weaken the fail-closed contract for operators who enable networkPolicy without ingress).
- **Chosen**: `values-prod.yaml` ships the documented working label (`kubernetes.io/metadata.name: ingress-nginx`, exactly what the chart README already recommends) plus a NOTES.txt warning for the mis-combination, so the shipped prod overlay works out of the box and hand-rolled overlays get a loud install-time signal.

### 2. Sticky routing default: snippet-free `upstream-hash-by "$http_x_codex_session_id$http_authorization"`

ingress-nginx ships `allow-snippet-annotations=false` since v1.9 and `annotations-risk-level=High` since v1.12 (`configuration-snippet` is risk Critical, so it is admission-rejected), meaning the current default blocks `helm install` of the prod overlay on stock controllers. `upstream-hash-by` is admitted at the default risk level, and undefined nginx `$http_*` variables evaluate to empty string, so the composite key hashes by session id (+auth) when the header is present and by Authorization alone otherwise — the same key the main API ingress already uses.

Accepted trade-off (documented in context.md): requests without `x-codex-session-id` lose the old per-request `$request_id` spread and pin one API key to one pod; correctness is unaffected either way because the DB-backed bridge ring owner-forwarding handles non-sticky arrivals — stickiness is an optimization, and the ring is the correctness mechanism. Snippet mode remains available by explicitly setting `configurationSnippet`, with required controller flags documented.

- **Rejected**: nginx cookie-based session affinity (Codex CLI clients do not retain cookies); keeping the snippet default + docs-only (leaves the default prod install broken).

### 3. Annotation coherence: one gate (`ingress.nginx.enabled`) for the whole nginx annotation set

Today streaming-safety annotations are gated but hash annotations render unconditionally, producing the staging overlay's wrong-half combination (sticky routing + 60s `proxy-read-timeout` + 1m body cap on a streaming proxy).

- **Rejected**: un-gating everything (dumps nginx annotations on non-nginx controllers); leaving templates alone and only fixing overlays (the trap remains for user values).
- **Chosen**: gate both halves together and set `ingress.nginx.enabled=true` in `values-staging.yaml` and `values-prod.yaml`. This is a behavior change for operators who enabled `ingress.enabled` and relied on the unconditional hash annotation without setting `ingress.nginx.enabled` — captured as a normative spec delta and release-notable risk.

### 4. README examples fixed to shapes that provably pass `Settings._validate_http_bridge_instance_configuration`

The chart injects `config.sessionBridgeAdvertiseBaseUrl` via the container `env:` list AFTER defining `POD_NAME`/`POD_NAMESPACE`/`POD_IP`, so `$(POD_NAME)` kubelet expansion already works with zero chart changes — the README just never used it. The advertise example becomes `http://$(POD_NAME).<headless>.<ns>.svc.<clusterDomain>:2455` (first hostname label == instance id, so `_bridge_advertise_hostname_is_replica_specific` passes); the ring example becomes bare pod names matching `$(POD_NAME)` (Settings requires the literal `instance_id in ring`).

- **Rejected**: relaxing settings.py validation — the validation is correct and the examples were wrong.

### 5. Static-ring render guard is a template `fail`, not a NOTES warning

`sessionBridgeInstanceRing` non-empty with `autoscaling.enabled` (prod overlay: maxReplicas=20), or with entries that do not exactly match the expected StatefulSet pod names (missing pods crashloop at Settings load; count alone is insufficient since a right-count/wrong-values ring crashloops too); failing at `helm template`/`upgrade` time is strictly better than a stalled rollout. The guard only fires on the opt-in manual-override path, so default installs are unaffected.

### 6. Two-replica smoke goes in external-db mode only

External-db is the PostgreSQL/multi-replica-shaped path (bundled stays 1 replica to bound kind CI cost). `helm --wait` on the StatefulSet already requires both pods Ready; we add an explicit ring assertion (`kubectl exec` pod-0 -> GET `/health/ready`, parse `bridge_ring.ring_size==2 && is_member`) because readiness gating on ring membership is precisely the machinery (parallel dual registration, headless-DNS advertise reachability, `publishNotReadyAddresses` handoff) that has never been exercised pre-release. This IS the two-replica simulation for this change: two real app instances sharing one PostgreSQL, at the actual product path (helm chart bring-up). The existing synced spec scenario "External DB smoke uses a single app replica" is MODIFIED to its two-replica replacement.

### 7. Compose guardrail is documentation-only

Both compose files publish host ports, so `docker compose up --scale server=2` already fails loudly on port collision; the guardrail comment (single-replica topology; multi-replica requires Helm + PostgreSQL) closes the operator-guidance gap without inventing runtime detection. Runtime SQLite multi-writer enforcement belongs to the sibling `document-replica-topology-contract` / leader-election changes — deliberately out of scope here to keep one concern per PR.

## Non-goals / zero-impact notes

- **Tables/columns/migrations**: none — zero schema impact.
- **Locks/CAS**: none needed.
- **SQLite vs PostgreSQL**: no runtime behavior change on either backend; the chart remains a PostgreSQL-only topology (bundled subchart or external DB) and the compose/README guardrails restate SQLite as single-instance-only.
- **Hot-path overhead**: zero — no per-request or app-code changes at all.

## Deviations from the reviewed design

None. Implementation follows the design as written. (`make helm-check` could not run `kubeconform` locally — the binary is not installed in this environment; `helm-lint` and `helm-template` across all overlays pass, and CI runs the full `helm-check`.)
