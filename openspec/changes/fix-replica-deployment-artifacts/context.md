# Context: fix-replica-deployment-artifacts

## Verified defects this change fixes

All six were adversarially verified against rendered chart output (`helm template`) and `Settings()` behavior:

1. **values-prod.yaml NetworkPolicy outage (high)**: `networkPolicy.enabled=true` + `ingress.enabled=true` with empty `ingressNSMatchLabels` renders a fail-closed policy that denies the ingress controller on port 2455. Kubelet probes bypass NetworkPolicy on mainstream CNIs and the helm-test pod is explicitly allowed, so pods stay Ready and `helm test` passes while every external request times out — a day-one fleet-wide outage with green signals on any policy-enforcing CNI.
2. **README advertise-URL example crashloops pods (medium)**: the shared-service-hostname example fails `Settings._validate_http_bridge_instance_configuration` ("must be replica-specific for bridge routing") on every pod that receives it; the rollout stalls at the first crashlooping pod.
3. **Responses sticky ingress admission-rejected (medium)**: the default `configuration-snippet` is refused by stock ingress-nginx >= 1.12 (`allow-snippet-annotations=false` since v1.9; `annotations-risk-level=High` since v1.12, snippet is Critical), so `helm install` of the prod overlay fails at the admission webhook.
4. **README manual-ring example crashloops pods + static ring bricks HPA scale-up (medium)**: FQDN ring entries never match the bare `$(POD_NAME)` instance id (literal membership check); with a correct N-name ring, any HPA scale-up beyond N creates pods that die at Settings load while the HPA keeps them desired.
5. **Split nginx annotation set (medium)**: streaming-safety annotations were gated on `ingress.nginx.enabled` while hash annotations rendered unconditionally; `values-staging.yaml` therefore got sticky routing with controller defaults — 60s `proxy-read-timeout` and 1m body cap — on an SSE/WebSocket proxy (the repo's WS-413 incident showed real bodies up to 128MiB).
6. **No CI coverage of the default two-replica topology (low)**: the chart defaults to `replicaCount=2` and readiness gates on bridge ring membership, yet both kind smoke paths ran one replica, so parallel dual registration, headless-DNS advertise reachability, and `publishNotReadyAddresses` handoff were never exercised pre-release.

## Sticky-key trade-off (accepted)

The new default `upstream-hash-by "$http_x_codex_session_id$http_authorization"` relies on undefined nginx `$http_*` variables rendering as empty strings: requests carrying `x-codex-session-id` hash by session (+API key); requests without it hash by the Authorization header alone. Compared with the old snippet (`$http_authorization:$request_id` fallback), no-session-header traffic loses the per-request spread and pins one API key to one pod (hot-key concentration). Correctness is unaffected in both directions: the DB-backed bridge ring owner-forwarding handles non-sticky arrivals — ingress stickiness is an optimization, the ring is the correctness mechanism. Upgrading re-hashes existing sessions once (one owner-forward hop per warm session).

## Snippet-mode controller requirements (opt-in)

Setting `ingress.responses.nginx.configurationSnippet` requires the ingress-nginx controller to run with `--allow-snippet-annotations=true` AND `annotations-risk-level: Critical`. When using a snippet-defined variable, also point `upstreamHashBy` at that variable (example preserved in `values.yaml` comments).

## Annotation-gating migration note (release-notable)

Operators who set `ingress.enabled=true` on an nginx class WITHOUT `ingress.nginx.enabled=true` previously received `upstream-hash-by` unconditionally. After this change they receive no nginx annotations until they set `ingress.nginx.enabled=true`. Session correctness is preserved by ring owner-forwarding; the cost is a forwarding hop per non-sticky arrival. Shipped overlays (`values-staging.yaml`, `values-prod.yaml`) now set the flag.

## NetworkPolicy allowlist assumption

`values-prod.yaml` ships `ingressNSMatchLabels: {kubernetes.io/metadata.name: ingress-nginx}` — the namespace-name well-known label for an ingress-nginx controller installed in the `ingress-nginx` namespace. Operators running a different controller or namespace must override the labels (documented in the values comment, README, and the new NOTES warning).

## Residual risks

- `upstream-hash-by` admission at the default `annotations-risk-level=High` is asserted from ingress-nginx documentation, not exercised in CI (the kind smoke does not run a real ingress-nginx admission webhook).
- The two-replica external-db kind smoke roughly doubles that mode's pod resources and adds ring-convergence timing; mitigated by keeping bundled mode at 1 replica, bounded `kubectl wait` timeouts, and the existing `dump_namespace_debug` ERR trap.
- The static-ring render guard turns a previously renderable (but crashlooping) config into a `helm upgrade` error — intended, but loud.
- Runtime multi-writer enforcement for compose/SQLite is deliberately out of scope (see sibling changes `document-replica-topology-contract` and `harden-scheduler-leader-election`).
