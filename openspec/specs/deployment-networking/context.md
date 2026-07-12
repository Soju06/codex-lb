# Deployment Networking Context

## Purpose and Scope

This capability covers the network defaults used by stock codex-lb deployments. See `spec.md` for the normative contracts. The operational goal is to let containers follow host resolver changes without bypassing VPN, split-DNS, or enterprise DNS policy.

## Why the Docker Network Matters

On Linux, a container started on Docker's legacy default `bridge` can receive the host's current resolver address directly in `/etc/resolv.conf`. If that address came from Wi-Fi DHCP, the running container can retain it after the host joins another network. The host may resolve names normally while the container repeatedly fails with `socket.gaierror: Temporary failure in name resolution` until it is recreated or attached to a user-defined network.

Containers on a user-defined bridge query Docker's embedded resolver at `127.0.0.11`. Docker then forwards queries according to the daemon and host's current resolver policy. This is preferable to hard-coding a public resolver, which may be unreachable or may bypass private DNS zones and VPN policy.

Application-level recovery complements this deployment default. A classified DNS or host-route failure rotates stale shared connector state and retries only replay-safe, pre-visible Responses work within the existing request deadline. It does not replay output already shown to the client, extend request budgets, or move continuation/file ownership to another account.

## Failure Modes and Constraints

- Docker's embedded resolver still depends on the Docker daemon and the host resolver path; a daemon-wide DNS failure cannot be repaired solely inside codex-lb.
- Long host outages end with the existing proxy request-timeout contract.
- Connection refusal, reset, TLS failure, proxy endpoint failure, and upstream HTTP errors are not classified as host-wide network roaming failures.
- Existing standalone containers remain on their current network until an operator attaches or recreates them. Future `docker run` invocations should include `--network codex-lb-net`.

## Diagnostics and Recovery

Compare host and container resolution before attributing the outage to an account or upstream service:

```bash
resolvectl query chatgpt.com
docker exec codex-lb getent ahostsv4 chatgpt.com
docker exec codex-lb cat /etc/resolv.conf
```

For the stock standalone network, `/etc/resolv.conf` should name `127.0.0.11`. To repair an existing running container without restarting it:

```bash
docker network inspect codex-lb-net >/dev/null 2>&1 || docker network create codex-lb-net
docker network connect codex-lb-net codex-lb
docker exec codex-lb getent ahostsv4 chatgpt.com
```

`docker network connect` reports an error if the container is already attached; inspect first when scripting repeated remediation. Runtime logs use the `process_network_recovery` marker with low-cardinality stages such as `detected`, `retrying`, `recovered`, and `exhausted`. They intentionally omit resolver addresses, request bodies, tokens, raw continuity keys, and account email addresses.
