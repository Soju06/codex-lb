# Kubernetes

Install with Helm:

```bash
helm install codex-lb oci://ghcr.io/soju06/charts/codex-lb \
  --set postgresql.auth.password=changeme \
  --set config.databaseMigrateOnStartup=true \
  --set migration.schemaGate.enabled=false
kubectl port-forward svc/codex-lb 2455:2455
```

Open [localhost:2455](http://localhost:2455) → Add account → Done.

## Multi-replica behavior

The Helm chart auto-configures HTTP `/responses` owner handoff for multi-replica installs using a headless-service DNS name per pod. The default cluster domain is `cluster.local`; set Helm `clusterDomain` if your cluster uses a different suffix. Override `config.sessionBridgeAdvertiseBaseUrl` only if pods must be reached through a different internal address.

In multi-replica setups, replicas must share the same encryption key (the Helm chart default) for bootstrap-token restart recovery and encrypted-data access to work.

## Full chart reference

For external database, production config, ingress, observability, and more see the
[Helm chart README](https://github.com/Soju06/codex-lb/blob/main/deploy/helm/codex-lb/README.md).

---

*Specs: [deployment-installation](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation) · [replica-operations](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/replica-operations)*
