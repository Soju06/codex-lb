import { useState } from "react";
import { Network } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { SettingsUpdateRequest, UpstreamProxyAdmin } from "@/features/settings/schemas";
import type {
  UpstreamProxyEndpointCreateRequest,
  UpstreamProxyPoolCreateRequest,
  UpstreamProxyPoolMemberRequest,
} from "@/features/settings/schemas";

const NO_POOL_VALUE = "__none__";

export type UpstreamProxySettingsProps = {
  admin: UpstreamProxyAdmin;
  busy: boolean;
  onSaveSettings: (payload: SettingsUpdateRequest) => Promise<void>;
  onCreateEndpoint: (payload: UpstreamProxyEndpointCreateRequest) => Promise<unknown>;
  onCreatePool: (payload: UpstreamProxyPoolCreateRequest) => Promise<unknown>;
  onAddPoolMember: (poolId: string, payload: UpstreamProxyPoolMemberRequest) => Promise<unknown>;
};

export function UpstreamProxySettings({
  admin,
  busy,
  onSaveSettings,
  onCreateEndpoint,
  onCreatePool,
  onAddPoolMember,
}: UpstreamProxySettingsProps) {
  const [endpointName, setEndpointName] = useState("");
  const [endpointScheme, setEndpointScheme] = useState<UpstreamProxyEndpointCreateRequest["scheme"]>("http");
  const [endpointHost, setEndpointHost] = useState("");
  const [endpointPort, setEndpointPort] = useState("8080");
  const [endpointUsername, setEndpointUsername] = useState("");
  const [endpointPassword, setEndpointPassword] = useState("");
  const [poolName, setPoolName] = useState("");
  const [selectedEndpointIds, setSelectedEndpointIds] = useState<Set<string>>(new Set());
  const [memberPoolId, setMemberPoolId] = useState(admin.pools[0]?.id ?? "");
  const [memberEndpointId, setMemberEndpointId] = useState(admin.endpoints[0]?.id ?? "");

  const endpointPortNumber = Number(endpointPort);
  const endpointValid =
    endpointName.trim().length > 0 &&
    endpointHost.trim().length > 0 &&
    Number.isInteger(endpointPortNumber) &&
    endpointPortNumber >= 1 &&
    endpointPortNumber <= 65535;
  const poolValid = poolName.trim().length > 0;
  const selectedMemberPool = admin.pools.find((pool) => pool.id === memberPoolId) ?? null;
  const memberEndpointAlreadyPresent = selectedMemberPool?.endpointIds.includes(memberEndpointId) ?? false;
  const memberValid = Boolean(memberPoolId && memberEndpointId && !memberEndpointAlreadyPresent);

  const toggleEndpointSelection = (endpointId: string, checked: boolean) => {
    setSelectedEndpointIds((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(endpointId);
      } else {
        next.delete(endpointId);
      }
      return next;
    });
  };

  const submitEndpoint = async () => {
    if (!endpointValid) {
      return;
    }
    await onCreateEndpoint({
      name: endpointName.trim(),
      scheme: endpointScheme,
      host: endpointHost.trim(),
      port: endpointPortNumber,
      username: endpointUsername.trim() || null,
      password: endpointPassword || null,
      isActive: true,
    });
    setEndpointName("");
    setEndpointHost("");
    setEndpointUsername("");
    setEndpointPassword("");
  };

  const submitPool = async () => {
    if (!poolValid) {
      return;
    }
    await onCreatePool({
      name: poolName.trim(),
      endpointIds: [...selectedEndpointIds],
      isActive: true,
    });
    setPoolName("");
    setSelectedEndpointIds(new Set());
  };

  const submitMember = async () => {
    if (!memberValid) {
      return;
    }
    await onAddPoolMember(memberPoolId, {
      endpointId: memberEndpointId,
      sortOrder: selectedMemberPool?.endpointIds.length ?? 0,
      weight: 1,
      isActive: true,
    });
  };

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <Network className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">Upstream proxy routing</h3>
              <p className="text-xs text-muted-foreground">
                Configure proxy pools used for account-bound ChatGPT upstream traffic.
              </p>
            </div>
          </div>
          <Switch
            aria-label="Enable upstream proxy routing"
            checked={admin.routingEnabled}
            disabled={busy}
            onCheckedChange={(checked) =>
              void onSaveSettings({ upstreamProxyRoutingEnabled: checked })
            }
          />
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border p-3">
            <p className="text-sm font-medium">Default pool</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Used only when routing is enabled and an account has no explicit binding.
            </p>
            <Select
              value={admin.defaultPoolId ?? NO_POOL_VALUE}
              onValueChange={(value) =>
                void onSaveSettings({ upstreamProxyDefaultPoolId: value === NO_POOL_VALUE ? null : value })
              }
              disabled={busy}
            >
              <SelectTrigger className="mt-3 h-8 text-xs" aria-label="Default proxy pool">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_POOL_VALUE}>No default pool</SelectItem>
                {admin.pools.map((pool) => (
                  <SelectItem key={pool.id} value={pool.id}>{pool.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="rounded-lg border p-3">
            <p className="text-sm font-medium">Current pools</p>
            <div className="mt-2 space-y-1 text-xs text-muted-foreground">
              {admin.pools.length === 0 ? <p>No proxy pools configured.</p> : null}
              {admin.pools.map((pool) => (
                <p key={pool.id}>
                  <span className="font-medium text-foreground">{pool.name}</span>{" "}
                  {pool.isActive ? "active" : "inactive"} · {pool.endpointIds.length} endpoint(s)
                </p>
              ))}
            </div>
          </div>
        </div>

        <div className="grid gap-3 lg:grid-cols-3">
          <div className="space-y-2 rounded-lg border p-3">
            <p className="text-sm font-medium">Create endpoint</p>
            <Input aria-label="Proxy endpoint name" className="h-8 text-xs" placeholder="Endpoint name" value={endpointName} disabled={busy} onChange={(event) => setEndpointName(event.target.value)} />
            <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-2">
              <Select value={endpointScheme} onValueChange={(value) => setEndpointScheme(value as UpstreamProxyEndpointCreateRequest["scheme"])} disabled={busy}>
                <SelectTrigger className="h-8 text-xs" aria-label="Proxy endpoint scheme"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="http">http</SelectItem>
                  <SelectItem value="https">https</SelectItem>
                  <SelectItem value="socks5">socks5</SelectItem>
                  <SelectItem value="socks5h">socks5h</SelectItem>
                </SelectContent>
              </Select>
              <Input aria-label="Proxy endpoint host" className="h-8 text-xs" placeholder="Host" value={endpointHost} disabled={busy} onChange={(event) => setEndpointHost(event.target.value)} />
            </div>
            <Input aria-label="Proxy endpoint port" className="h-8 text-xs" inputMode="numeric" placeholder="Port" value={endpointPort} disabled={busy} onChange={(event) => setEndpointPort(event.target.value)} />
            <Input aria-label="Proxy endpoint username" className="h-8 text-xs" placeholder="Username (optional)" value={endpointUsername} disabled={busy} onChange={(event) => setEndpointUsername(event.target.value)} />
            <Input aria-label="Proxy endpoint password" className="h-8 text-xs" placeholder="Password (optional)" type="password" value={endpointPassword} disabled={busy} onChange={(event) => setEndpointPassword(event.target.value)} />
            <Button type="button" size="sm" className="h-8 w-full text-xs" disabled={busy || !endpointValid} onClick={() => void submitEndpoint()}>
              Create endpoint
            </Button>
          </div>

          <div className="space-y-2 rounded-lg border p-3">
            <p className="text-sm font-medium">Create pool</p>
            <Input aria-label="Proxy pool name" className="h-8 text-xs" placeholder="Pool name" value={poolName} disabled={busy} onChange={(event) => setPoolName(event.target.value)} />
            <div className="max-h-40 space-y-2 overflow-auto rounded-md border p-2">
              {admin.endpoints.length === 0 ? <p className="text-xs text-muted-foreground">Create an endpoint first.</p> : null}
              {admin.endpoints.map((endpoint) => (
                <label key={endpoint.id} className="flex items-center gap-2 text-xs">
                  <Checkbox
                    checked={selectedEndpointIds.has(endpoint.id)}
                    disabled={busy}
                    onCheckedChange={(checked) => toggleEndpointSelection(endpoint.id, checked === true)}
                  />
                  <span>{endpoint.name} · {endpoint.scheme}://{endpoint.host}:{endpoint.port}</span>
                </label>
              ))}
            </div>
            <Button type="button" size="sm" className="h-8 w-full text-xs" disabled={busy || !poolValid} onClick={() => void submitPool()}>
              Create pool
            </Button>
          </div>

          <div className="space-y-2 rounded-lg border p-3">
            <p className="text-sm font-medium">Add pool member</p>
            <Select value={memberPoolId} onValueChange={setMemberPoolId} disabled={busy || admin.pools.length === 0}>
              <SelectTrigger className="h-8 text-xs" aria-label="Pool member pool"><SelectValue placeholder="Select pool" /></SelectTrigger>
              <SelectContent>{admin.pools.map((pool) => <SelectItem key={pool.id} value={pool.id}>{pool.name}</SelectItem>)}</SelectContent>
            </Select>
            <Select value={memberEndpointId} onValueChange={setMemberEndpointId} disabled={busy || admin.endpoints.length === 0}>
              <SelectTrigger className="h-8 text-xs" aria-label="Pool member endpoint"><SelectValue placeholder="Select endpoint" /></SelectTrigger>
              <SelectContent>{admin.endpoints.map((endpoint) => <SelectItem key={endpoint.id} value={endpoint.id}>{endpoint.name}</SelectItem>)}</SelectContent>
            </Select>
            {memberEndpointAlreadyPresent ? (
              <AlertMessage variant="warning">Endpoint is already in {selectedMemberPool?.name}.</AlertMessage>
            ) : null}
            <Button type="button" size="sm" className="h-8 w-full text-xs" disabled={busy || !memberValid} onClick={() => void submitMember()}>
              Add member
            </Button>
          </div>
        </div>

        <div className="rounded-lg border p-3">
          <p className="text-sm font-medium">Endpoints</p>
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            {admin.endpoints.length === 0 ? <p className="text-xs text-muted-foreground">No proxy endpoints configured.</p> : null}
            {admin.endpoints.map((endpoint) => (
              <div key={endpoint.id} className="rounded-md bg-muted/50 px-2 py-1.5 text-xs">
                <span className="font-medium">{endpoint.name}</span> · {endpoint.scheme}://{endpoint.username ? `${endpoint.username}@` : ""}{endpoint.host}:{endpoint.port}
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
