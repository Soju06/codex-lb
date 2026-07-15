# Tasks: fix-replica-deployment-artifacts

## 1. OpenSpec artifacts

- [x] 1.1 Create proposal.md, design.md, tasks.md, context.md, and delta specs for `deployment-networking` and `deployment-installation`; run `openspec validate fix-replica-deployment-artifacts`

## 2. Chart values overlays

- [x] 2.1 values-prod.yaml: add `networkPolicy.ingressNSMatchLabels` (`kubernetes.io/metadata.name: ingress-nginx`, with adjust-for-your-controller comment) and `ingress.nginx.enabled=true`
- [x] 2.2 values-staging.yaml: add `ingress.nginx.enabled=true`

## 3. Chart templates

- [x] 3.1 templates/NOTES.txt: WARNING block for `networkPolicy.enabled && ingress.enabled && empty ingressNSMatchLabels && empty extraIngress`
- [x] 3.2 templates/ingress.yaml: move `upstream-hash-by` / responses sticky annotation emission under the `ingress.nginx.enabled` gate (single coherent gate for all nginx annotations)
- [x] 3.3 values.yaml: default `ingress.responses.nginx.configurationSnippet` to `""` and `ingress.responses.nginx.upstreamHashBy` to `"$http_x_codex_session_id$http_authorization"`; update @param comments documenting the opt-in snippet mode and its required controller flags
- [x] 3.4 templates/deployment.yaml: render-time `fail` when `config.sessionBridgeInstanceRing` is non-empty and (`autoscaling.enabled` or the trimmed ring entries do not exactly match the expected StatefulSet pod names `<workload-name>-0..<replicaCount-1>`), with actionable error messages naming missing/unexpected entries (value validation, not just entry count)

## 4. Documentation

- [x] 4.1 deploy/helm/codex-lb/README.md: fix `sessionBridgeAdvertiseBaseUrl` example to `http://$(POD_NAME).<headless-svc>.<ns>.svc.<clusterDomain>:2455` with env-expansion explanation; fix Manual Ring Override example to bare `<release>-workload-N` pod names; document static-ring vs autoscaling incompatibility; document snippet-mode controller requirements and the snippet-free default; cross-reference the NetworkPolicy `ingressNSMatchLabels` requirement from the prod overlay section
- [x] 4.2 docker-compose.yml + docker-compose.prod.yml: add single-replica guardrail header comment (no `--scale`; multi-replica requires Helm chart + PostgreSQL + leader election)

## 5. Smoke coverage

- [x] 5.1 scripts/helm-kind-smoke.sh: external-db mode `--set replicaCount=2`; assert 2 Ready pods (kubectl wait) and kubectl-exec pod-0 GET `/health/ready` asserting `bridge_ring.ring_size==2` and `is_member==true`

## 6. Tests

- [x] 6.1 Add tests/unit/test_helm_replica_artifacts.py: NetworkPolicy allow rule + fail-closed invariant; NOTES warning present/absent; coherent nginx annotation set on staging/prod; no nginx annotations without the flag; snippet-free default + explicit snippet opt-in; render-guard failure and success cases; README examples pass Settings validation; smoke-script two-replica assertion; compose guardrail grep
- [x] 6.2 Update tests/unit/test_helm_external_secrets.py for the snippet-free default and two-replica smoke topology
- [x] 6.3 Run `make helm-lint helm-template` and `uv run pytest tests/unit/test_helm_replica_artifacts.py tests/unit/test_helm_external_secrets.py tests/unit/test_k8s_version_policy.py`

## 7. Follow-up (post-verification)

- [x] 7.1 Sync delta specs into main specs via `/opsx:sync` after the openspec-sot-sync PR lands (this change's MODIFIED requirement targets the synced SSOT text) — done during archive (`openspec archive` synced the delta; the MODIFIED requirement is now present in `openspec/specs/deployment-networking/spec.md`)
