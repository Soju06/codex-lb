# Deployment Networking Context

## Purpose and Scope

This capability covers the network defaults used by stock codex-lb deployments. See `spec.md` for the normative contracts. The operational goal is to let containers follow host resolver changes without bypassing VPN, split-DNS, or enterprise DNS policy.

## Why the Docker Network Matters

On Linux, a container started on Docker's legacy default `bridge` can receive the host's current resolver address directly in `/etc/resolv.conf`. If that address came from Wi-Fi DHCP, the running container can retain it after the host joins another network. The host may resolve names normally while the container repeatedly fails with `socket.gaierror: Temporary failure in name resolution`.

Containers on a user-defined bridge query Docker's embedded resolver at `127.0.0.11`. This avoids placing one Wi-Fi DNS address directly in the container configuration, but it does not guarantee recovery after switching networks. Live validation on Ubuntu with Docker Engine showed that the embedded resolver retained the old network's external forwarding servers from 13:52 until the container restarted at 13:54. After restart, the generated resolver metadata changed from the old Wi-Fi servers to `192.168.110.1` and resolution recovered.

Host networking on Docker Engine for Linux avoids that Docker forwarding layer, but it is useful only when the host resolver address itself is stable. On the same machine, a temporary host-network container received `nameserver 127.0.0.53` and successfully resolved `chatgpt.com` through the live `systemd-resolved` stub. A host whose `/etc/resolv.conf` points directly to a DNS server supplied by DHCP can retain that stale address even in host-network mode; such a host first needs a stable local resolver, the bridge-listener runbook below, or the native `uvx` deployment. Host networking removes Docker network-namespace isolation, so it is an explicit operator choice rather than the portable default. Docker Desktop 4.34 and later also offers opt-in host networking for Linux containers, but its layer-4, VM-backed implementation differs and this change has not verified it as a DNS-recovery guarantee after switching networks.

Application-level recovery complements this deployment default. A classified DNS or host-route failure rotates stale shared connector state and retries only when the transport proves the failure happened before request dispatch, within the existing request deadline. A post-connect send or receive failure and a serialized terminal response may be account-neutral, but neither is replayed because upstream delivery is uncertain. Recovery does not extend request budgets or move continuation/file ownership to another account.

## Failure Modes and Constraints

- Docker's embedded resolver can retain stale external forwarders even while `/etc/resolv.conf` names `127.0.0.11`; rebuilding an aiohttp client cannot repair that Docker state.
- Application recovery prevents account-health poisoning and preserves replay-safe work, but a persistent resolver outage still ends at the existing request budget.
- Long host outages end with the existing proxy request-timeout contract.
- Connection refusal, reset, TLS failure, proxy endpoint failure, and upstream HTTP errors are not classified as host-wide network-switch failures.
- The verified Docker Engine host-network option is Linux-specific and depends on a stable host resolver address; a direct DHCP-provided DNS address may still become stale. It does not use `-p` and exposes codex-lb directly in the host network namespace; Docker Desktop support has different limitations.

## Diagnostics and Recovery

Compare host and container resolution before attributing the outage to an account or upstream service:

```bash
resolvectl query chatgpt.com
docker exec codex-lb getent ahostsv4 chatgpt.com
docker exec codex-lb cat /etc/resolv.conf
```

For portable bridge mode, `/etc/resolv.conf` normally names `127.0.0.11`. Compare the `ExtServers` comment before and after switching networks; an old Wi-Fi address there explains why the embedded resolver itself is reachable but external queries fail.

An existing systemd-resolved host can give a running container a stable resolver without restarting codex-lb. Determine the user-defined bridge gateway, expose the host stub only on that bridge address, reload systemd-resolved, and repoint the container resolver as root:

```bash
gateway="$(docker network inspect codex-lb-net --format '{{(index .IPAM.Config 0).Gateway}}')"
sudo install -d -m 0755 /etc/systemd/resolved.conf.d
printf '[Resolve]\nDNSStubListenerExtra=%s\n' "$gateway" \
  | sudo tee /etc/systemd/resolved.conf.d/codex-lb-docker.conf >/dev/null
sudo systemctl reload systemd-resolved
docker exec --user 0 codex-lb sh -c \
  "printf 'nameserver %s\noptions edns0 trust-ad\n' '$gateway' > /etc/resolv.conf"
docker exec codex-lb getent ahostsv4 chatgpt.com
```

The listener exposes host DNS to containers that can reach that bridge, so limit it to the bridge gateway rather than `0.0.0.0`. A future container recreation must pass `--dns "$gateway"` or reapply the resolver override. Operators whose host already has a stable local resolver can instead recreate once with the documented Linux `--network host` launch.

Runtime logs use the `process_network_recovery` marker with low-cardinality stages such as `detected`, `retrying`, `recovered`, and `exhausted`. They intentionally omit resolver addresses, request bodies, tokens, raw continuity keys, and account email addresses.
