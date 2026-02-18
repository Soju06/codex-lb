import { useMemo, useState } from "react";
import { RefreshCw, Shield } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SpinnerBlock } from "@/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useFirewall } from "@/features/firewall/hooks/use-firewall";
import { formatTimeLong } from "@/utils/formatters";

function modeLabel(mode: "allow_all" | "allowlist_active"): string {
  return mode === "allow_all" ? "Allow all" : "Allowlist active";
}

export function FirewallPage() {
  const [ipAddress, setIpAddress] = useState("");
  const { firewallQuery, createMutation, deleteMutation } = useFirewall();

  const errorMessage = useMemo(() => {
    if (firewallQuery.error instanceof Error) {
      return firewallQuery.error.message;
    }
    if (createMutation.error instanceof Error) {
      return createMutation.error.message;
    }
    if (deleteMutation.error instanceof Error) {
      return deleteMutation.error.message;
    }
    return null;
  }, [firewallQuery.error, createMutation.error, deleteMutation.error]);

  const entries = firewallQuery.data?.entries ?? [];
  const mode = firewallQuery.data?.mode ?? "allow_all";
  const busy = createMutation.isPending || deleteMutation.isPending;
  const refreshing = firewallQuery.isFetching;

  const handleAdd = async () => {
    const normalized = ipAddress.trim();
    if (!normalized) {
      return;
    }
    await createMutation.mutateAsync(normalized);
    setIpAddress("");
  };

  const handleRemove = async (value: string) => {
    if (!window.confirm(`Remove ${value} from firewall allowlist?`)) {
      return;
    }
    await deleteMutation.mutateAsync(value);
  };

  return (
    <div className="animate-fade-in-up space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Shield className="h-5 w-5 text-primary" />
          Firewall
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Restrict proxy APIs to allowed client IPs.
        </p>
      </div>

      {errorMessage ? <AlertMessage variant="error">{errorMessage}</AlertMessage> : null}

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Allowlist</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="rounded-md border border-border/70 p-3">
              <p className="text-xs text-muted-foreground">Mode</p>
              <div className="mt-1">
                <Badge variant="outline">{modeLabel(mode)}</Badge>
              </div>
            </div>
            <div className="rounded-md border border-border/70 p-3">
              <p className="text-xs text-muted-foreground">Allowed IPs</p>
              <p className="mt-1 text-sm font-medium">{entries.length}</p>
            </div>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={ipAddress}
              onChange={(event) => setIpAddress(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void handleAdd();
                }
              }}
              placeholder="127.0.0.1 or 2001:db8::1"
              disabled={busy}
            />
            <div className="flex gap-2">
              <Button type="button" onClick={() => void handleAdd()} disabled={busy || !ipAddress.trim()}>
                Add IP
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => void firewallQuery.refetch()}
                disabled={refreshing || busy}
              >
                <RefreshCw className={`mr-1.5 h-3.5 w-3.5${refreshing ? " animate-spin" : ""}`} />
                Refresh
              </Button>
            </div>
          </div>

          {firewallQuery.isLoading && !firewallQuery.data ? (
            <div className="py-8">
              <SpinnerBlock />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>IP Address</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="w-[96px] text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {entries.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={3} className="py-6 text-center text-sm text-muted-foreground">
                      Allowlist is empty. Firewall mode is currently allow-all.
                    </TableCell>
                  </TableRow>
                ) : (
                  entries.map((entry) => {
                    const created = formatTimeLong(entry.createdAt);
                    return (
                      <TableRow key={entry.ipAddress}>
                        <TableCell className="font-mono text-xs">{entry.ipAddress}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {created.date} {created.time}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className="text-destructive hover:text-destructive"
                            disabled={busy}
                            onClick={() => void handleRemove(entry.ipAddress)}
                          >
                            Remove
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
