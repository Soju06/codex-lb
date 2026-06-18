import { Boxes, Network, Plus, Server } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useDialogState } from "@/hooks/use-dialog-state";
import { ProxyEndpointCreateDialog } from "@/features/settings/components/proxy-endpoint-create-dialog";
import { ProxyPoolCreateDialog } from "@/features/settings/components/proxy-pool-create-dialog";
import { ProxyPoolMemberDialog } from "@/features/settings/components/proxy-pool-member-dialog";
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
  const endpointDialog = useDialogState();
  const poolDialog = useDialogState();
  const memberDialog = useDialogState();

  const hasEndpoints = admin.endpoints.length > 0;
  const hasPools = admin.pools.length > 0;

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
            onCheckedChange={(checked) => void onSaveSettings({ upstreamProxyRoutingEnabled: checked })}
          />
        </div>

        <div className="rounded-lg border p-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium">Default pool</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Used only when routing is enabled and an account has no explicit binding.
              </p>
            </div>
            <Select
              value={admin.defaultPoolId ?? NO_POOL_VALUE}
              onValueChange={(value) =>
                void onSaveSettings({ upstreamProxyDefaultPoolId: value === NO_POOL_VALUE ? null : value })
              }
              disabled={busy}
            >
              <SelectTrigger className="h-8 w-full min-w-0 text-xs sm:w-56" aria-label="Default proxy pool">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_POOL_VALUE}>No default pool</SelectItem>
                {admin.pools.map((pool) => (
                  <SelectItem key={pool.id} value={pool.id}>
                    {pool.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            className="h-8 gap-1.5 text-xs"
            disabled={busy}
            onClick={() => endpointDialog.show()}
          >
            <Plus className="h-3.5 w-3.5" />
            Add endpoint
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs"
            disabled={busy || !hasEndpoints}
            onClick={() => poolDialog.show()}
          >
            <Boxes className="h-3.5 w-3.5" />
            Create pool
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs"
            disabled={busy || !hasPools || !hasEndpoints}
            onClick={() => memberDialog.show()}
          >
            <Plus className="h-3.5 w-3.5" />
            Add member
          </Button>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="flex items-center gap-1.5 text-sm font-medium">
                <Server className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                Endpoints
              </p>
              <span className="text-xs tabular-nums text-muted-foreground">{admin.endpoints.length}</span>
            </div>
            <div className="mt-2 space-y-1.5">
              {hasEndpoints ? (
                admin.endpoints.map((endpoint) => (
                  <div key={endpoint.id} className="rounded-md bg-muted/50 px-2.5 py-1.5 text-xs">
                    <span className="font-medium text-foreground">{endpoint.name}</span>
                    <span className="text-muted-foreground">
                      {" "}
                      · {endpoint.scheme}://{endpoint.username ? `${endpoint.username}@` : ""}
                      {endpoint.host}:{endpoint.port}
                    </span>
                  </div>
                ))
              ) : (
                <p className="text-xs text-muted-foreground">No proxy endpoints configured.</p>
              )}
            </div>
          </div>

          <div className="rounded-lg border p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="flex items-center gap-1.5 text-sm font-medium">
                <Boxes className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                Pools
              </p>
              <span className="text-xs tabular-nums text-muted-foreground">{admin.pools.length}</span>
            </div>
            <div className="mt-2 space-y-1.5">
              {hasPools ? (
                admin.pools.map((pool) => (
                  <div
                    key={pool.id}
                    className="flex items-center justify-between gap-2 rounded-md bg-muted/50 px-2.5 py-1.5 text-xs"
                  >
                    <span className="min-w-0 truncate font-medium text-foreground">{pool.name}</span>
                    <span className="shrink-0 text-muted-foreground">
                      {pool.isActive ? "active" : "inactive"} · {pool.endpointIds.length} endpoint(s)
                    </span>
                  </div>
                ))
              ) : (
                <p className="text-xs text-muted-foreground">No proxy pools configured.</p>
              )}
            </div>
          </div>
        </div>
      </div>

      <ProxyEndpointCreateDialog
        open={endpointDialog.open}
        busy={busy}
        onOpenChange={endpointDialog.onOpenChange}
        onSubmit={onCreateEndpoint}
      />
      <ProxyPoolCreateDialog
        open={poolDialog.open}
        busy={busy}
        endpoints={admin.endpoints}
        onOpenChange={poolDialog.onOpenChange}
        onSubmit={onCreatePool}
      />
      <ProxyPoolMemberDialog
        open={memberDialog.open}
        busy={busy}
        pools={admin.pools}
        endpoints={admin.endpoints}
        onOpenChange={memberDialog.onOpenChange}
        onSubmit={onAddPoolMember}
      />
    </section>
  );
}
