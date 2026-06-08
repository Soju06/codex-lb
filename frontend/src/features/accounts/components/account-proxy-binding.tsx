import { useMemo, useState } from "react";
import { Network } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { AccountSummary } from "@/features/accounts/schemas";
import type { AccountProxyBindingRequest, UpstreamProxyAdmin } from "@/features/settings/schemas";

export type AccountProxyBindingProps = {
  account: AccountSummary;
  admin: UpstreamProxyAdmin | null;
  busy: boolean;
  onSave: (accountId: string, payload: AccountProxyBindingRequest) => Promise<unknown>;
};

export function AccountProxyBinding({ account, admin, busy, onSave }: AccountProxyBindingProps) {
  const binding = admin?.bindings.find((item) => item.accountId === account.accountId) ?? null;
  const initialPoolId = binding?.poolId ?? admin?.pools[0]?.id ?? "";
  const [selectedPoolId, setSelectedPoolId] = useState(initialPoolId);
  const poolsById = useMemo(() => new Map((admin?.pools ?? []).map((pool) => [pool.id, pool])), [admin?.pools]);
  const selectedPool = poolsById.get(selectedPoolId) ?? null;
  const active = binding?.isActive ?? false;

  if (!admin) {
    return null;
  }

  return (
    <section className="rounded-xl border bg-card p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <Network className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">Account proxy binding</h3>
            <p className="text-xs text-muted-foreground">
              Route this account's ChatGPT upstream traffic through a specific proxy pool.
            </p>
          </div>
        </div>
        <Switch
          aria-label="Enable account proxy binding"
          checked={active}
          disabled={busy || !binding}
          onCheckedChange={(checked) => {
            const poolId = binding?.poolId ?? selectedPoolId;
            if (!poolId) return;
            void onSave(account.accountId, { poolId, isActive: checked });
          }}
        />
      </div>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        <Select value={selectedPoolId} onValueChange={setSelectedPoolId} disabled={busy || admin.pools.length === 0}>
          <SelectTrigger className="h-8 text-xs" aria-label="Account proxy pool">
            <SelectValue placeholder="Select proxy pool" />
          </SelectTrigger>
          <SelectContent>
            {admin.pools.map((pool) => (
              <SelectItem key={pool.id} value={pool.id}>{pool.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 text-xs sm:w-28"
          disabled={busy || !selectedPoolId}
          onClick={() => void onSave(account.accountId, { poolId: selectedPoolId, isActive: true })}
        >
          Save binding
        </Button>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {binding
          ? `Current binding: ${poolsById.get(binding.poolId)?.name ?? binding.poolId} (${binding.isActive ? "active" : "disabled"}).`
          : "No account-specific proxy pool binding is configured."}
        {selectedPool ? ` Selected pool has ${selectedPool.endpointIds.length} endpoint(s).` : ""}
      </p>
    </section>
  );
}
