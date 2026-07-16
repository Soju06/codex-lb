# Fix Replica Deployment Artifacts

## Why

The shipped multi-replica deployment artifacts are broken in ways that either take the fleet down or silently degrade it while every probe stays green: `values-prod.yaml` enables a fail-closed NetworkPolicy with no ingress-controller allow rule (day-one external outage on any policy-enforcing CNI), both chart README bridge-ring examples deterministically fail Settings validation and crashloop pods, the responses sticky ingress depends on a `configuration-snippet` that stock ingress-nginx >= 1.12 rejects at admission, and the staging/prod overlays get sticky-hash routing without the streaming-safety annotations (60s read timeout / 1m body cap on SSE/WS paths). None of this is caught before release because both helm-kind-smoke paths run one replica while the chart defaults to two. All six defects were adversarially verified against rendered chart output and Settings() behavior.

## What Changes

- **values-prod.yaml**: add `networkPolicy.ingressNSMatchLabels` (`kubernetes.io/metadata.name: ingress-nginx`, with an adjust-for-your-controller comment) and set `ingress.nginx.enabled: true`.
- **values-staging.yaml**: set `ingress.nginx.enabled: true`.
- **templates/NOTES.txt**: render a loud WARNING when `networkPolicy.enabled` and `ingress.enabled` are set while both `ingressNSMatchLabels` and `extraIngress` are empty — external traffic will be denied on the HTTP port while probes stay green.
- **templates/ingress.yaml**: gate ALL nginx annotations (streaming-safety base set AND `upstream-hash-by` AND the responses sticky set) behind the single `ingress.nginx.enabled` flag so the two halves of the nginx contract can never be split again.
- **values.yaml**: default `ingress.responses.nginx.configurationSnippet` to `""` and `ingress.responses.nginx.upstreamHashBy` to `"$http_x_codex_session_id$http_authorization"` (snippet-free consistent hash; undefined nginx header vars render empty, so it degrades to Authorization-key hashing when no session header). Snippet mode stays available as an explicit opt-in with the required controller flags documented.
- **templates/deployment.yaml**: `fail` at render time when `config.sessionBridgeInstanceRing` is non-empty AND (`autoscaling.enabled=true` OR `replicaCount` > ring entry count) — turning the HPA-scale-up crashloop into a loud helm error.
- **chart README.md**: fix the advertise-URL override example to the per-pod pattern `http://$(POD_NAME).<headless-svc>.<ns>.svc.<clusterDomain>:2455`; fix the Manual Ring Override example to bare StatefulSet pod names; document static-ring/autoscaling incompatibility; document NetworkPolicy + snippet-mode controller requirements.
- **scripts/helm-kind-smoke.sh**: external-db mode installs with `--set replicaCount=2`, then asserts 2 Ready pods and, from inside pod 0, that `/health/ready` reports `bridge_ring.ring_size == 2` and `is_member == true`.
- **docker-compose.yml / docker-compose.prod.yml**: header-comment guardrail stating compose is a single-replica topology (do not `--scale`; multi-replica requires the Helm chart + PostgreSQL + leader election).
- **New regression tests** `tests/unit/test_helm_replica_artifacts.py` (chart render assertions, render-guard failures, README-example-passes-Settings-validation, smoke-script topology assertion).
- Spec deltas in `deployment-networking` (4 ADDED) and `deployment-installation` (1 MODIFIED, 3 ADDED).
- No app runtime code, no migrations, no settings.py changes.

## Impact

- Affected specs: `deployment-networking`, `deployment-installation`
- Affected code: `deploy/helm/codex-lb/**` (values overlays, ingress/deployment/NOTES templates, README), `scripts/helm-kind-smoke.sh`, `docker-compose.yml`, `docker-compose.prod.yml`, `tests/unit/test_helm_external_secrets.py`, new `tests/unit/test_helm_replica_artifacts.py`
- Behavior change for operators who set `ingress.enabled=true` on an nginx class without `ingress.nginx.enabled=true`: they previously received `upstream-hash-by` unconditionally and now receive no nginx annotations until they set the flag (session correctness is preserved by DB-backed bridge ring owner-forwarding; the cost is a forwarding hop). Called out in change notes.
