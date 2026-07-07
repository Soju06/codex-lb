import { useMemo, useState } from "react";
import { CheckCircle2, Loader2, Network, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { AccountSummary } from "@/features/accounts/schemas";
import type {
  AccountProxyBindingRequest,
  UpstreamProxyAdmin,
  UpstreamProxyEndpointTestResponse,
} from "@/features/settings/schemas";

export type AccountProxyBindingProps = {
  account: AccountSummary;
  admin: UpstreamProxyAdmin | null;
  busy: boolean;
  readOnly?: boolean;
  onSave: (accountId: string, payload: AccountProxyBindingRequest) => Promise<unknown>;
  onTestEndpoint?: (endpointId: string) => Promise<UpstreamProxyEndpointTestResponse>;
};

export function AccountProxyBinding({
  account,
  admin,
  busy,
  readOnly = false,
  onSave,
  onTestEndpoint,
}: AccountProxyBindingProps) {
  const binding = admin?.bindings.find((item) => item.accountId === account.accountId) ?? null;
  const initialPoolId = binding?.poolId ?? admin?.pools[0]?.id ?? "";
  const [selectedPoolId, setSelectedPoolId] = useState(initialPoolId);
  const [testingEndpointId, setTestingEndpointId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    endpointId: string;
    result: UpstreamProxyEndpointTestResponse;
  } | null>(null);
  const poolsById = useMemo(() => new Map((admin?.pools ?? []).map((pool) => [pool.id, pool])), [admin?.pools]);
  const endpointsById = useMemo(
    () => new Map((admin?.endpoints ?? []).map((endpoint) => [endpoint.id, endpoint])),
    [admin?.endpoints],
  );
  const currentPoolId = selectedPoolId && poolsById.has(selectedPoolId) ? selectedPoolId : initialPoolId;
  const selectedPool = poolsById.get(currentPoolId) ?? null;
  const selectedPoolEndpointId = selectedPool?.endpointIds[0] ?? null;
  const selectedPoolEndpoint = selectedPoolEndpointId ? (endpointsById.get(selectedPoolEndpointId) ?? null) : null;
  const active = binding?.isActive ?? false;

  if (!admin) {
    return null;
  }

  const testSelectedPool = async () => {
    if (!selectedPoolEndpointId || !onTestEndpoint || testingEndpointId !== null) {
      return;
    }
    setTestingEndpointId(selectedPoolEndpointId);
    setTestResult(null);
    try {
      const result = await onTestEndpoint(selectedPoolEndpointId);
      setTestResult({ endpointId: selectedPoolEndpointId, result });
    } catch (error) {
      setTestResult({
        endpointId: selectedPoolEndpointId,
        result: {
          endpointId: selectedPoolEndpointId,
          ok: false,
          statusCode: null,
          elapsedMs: null,
          error: error instanceof Error ? error.message : "Proxy test failed",
        },
      });
    } finally {
      setTestingEndpointId(null);
    }
  };

  return (
    <section className="min-w-0 rounded-lg border bg-muted/30 p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <Network className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">Account proxy binding</h3>
            <p className="text-xs text-muted-foreground">
              Route this account's ChatGPT upstream traffic through a specific proxy pool.
            </p>
          </div>
        </div>
        <Switch
          aria-label="Enable account proxy binding"
          className="shrink-0"
          checked={active}
          disabled={busy || readOnly || !binding}
          onCheckedChange={(checked) => {
            const poolId = binding?.poolId ?? currentPoolId;
            if (!poolId) return;
            void onSave(account.accountId, { poolId, isActive: checked });
          }}
        />
      </div>
      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        <Select
          value={currentPoolId}
          onValueChange={(poolId) => {
            setSelectedPoolId(poolId);
            setTestResult(null);
          }}
          disabled={busy || readOnly || admin.pools.length === 0}
        >
          <SelectTrigger className="h-8 w-full min-w-0 text-xs sm:w-auto sm:flex-1" aria-label="Account proxy pool">
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
          disabled={busy || readOnly || !currentPoolId}
          onClick={() => void onSave(account.accountId, { poolId: currentPoolId, isActive: true })}
        >
          Save binding
        </Button>
        {onTestEndpoint ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 text-xs sm:w-28"
            disabled={busy || readOnly || !selectedPoolEndpointId || testingEndpointId !== null}
            onClick={() => void testSelectedPool()}
          >
            {testingEndpointId === selectedPoolEndpointId ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden="true" />
            ) : null}
            Test pool
          </Button>
        ) : null}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {binding
          ? `Current binding: ${poolsById.get(binding.poolId)?.name ?? binding.poolId} (${binding.isActive ? "active" : "disabled"}).`
          : "No account-specific proxy pool binding is configured."}
        {selectedPool ? ` Selected pool has ${selectedPool.endpointIds.length} endpoint(s).` : ""}
        {selectedPoolEndpoint ? ` First endpoint: ${selectedPoolEndpoint.name}.` : ""}
      </p>
      {testResult && testResult.endpointId === selectedPoolEndpointId ? (
        <div
          className={
            testResult.result.ok
              ? "mt-2 flex items-center gap-1 text-xs text-emerald-600"
              : "mt-2 flex items-center gap-1 text-xs text-destructive"
          }
        >
          {testResult.result.ok ? (
            <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
          ) : (
            <XCircle className="h-3 w-3" aria-hidden="true" />
          )}
          <span>
            {testResult.result.ok ? "Connection ok" : "Connection failed"}
            {testResult.result.statusCode ? ` · HTTP ${testResult.result.statusCode}` : ""}
            {testResult.result.elapsedMs !== null && testResult.result.elapsedMs !== undefined
              ? ` · ${testResult.result.elapsedMs}ms`
              : ""}
            {!testResult.result.ok && testResult.result.error ? ` · ${testResult.result.error}` : ""}
          </span>
        </div>
      ) : null}
    </section>
  );
}
