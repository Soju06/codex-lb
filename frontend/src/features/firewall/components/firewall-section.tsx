import { useMemo, useState } from "react";
import { RefreshCw, Shield } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SpinnerBlock } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useFirewall } from "@/features/firewall/hooks/use-firewall";
import { useDialogState } from "@/hooks/use-dialog-state";
import { getErrorMessageOrNull } from "@/utils/errors";
import { formatTimeLong } from "@/utils/formatters";

function modeLabel(mode: "allow_all" | "allowlist_active"): string {
  return mode === "allow_all" ? "Allow all" : "Allowlist active";
}

export function FirewallSection() {
  const [ipAddress, setIpAddress] = useState("");
  const { firewallQuery, createMutation, deleteMutation } = useFirewall();
  const deleteDialog = useDialogState<string>();

  const mutationError = useMemo(
    () =>
      getErrorMessageOrNull(firewallQuery.error) ||
      getErrorMessageOrNull(createMutation.error) ||
      getErrorMessageOrNull(deleteMutation.error),
    [firewallQuery.error, createMutation.error, deleteMutation.error],
  );

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

  return (
    <section className="space-y-3 rounded-xl border bg-card p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <Shield className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">Firewall</h3>
            <p className="text-xs text-muted-foreground">Restrict proxy APIs to allowed client IPs.</p>
          </div>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 text-xs"
          onClick={() => void firewallQuery.refetch()}
          disabled={refreshing || busy}
        >
          <RefreshCw className={`mr-1.5 h-3.5 w-3.5${refreshing ? " animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {mutationError ? <AlertMessage variant="error">{mutationError}</AlertMessage> : null}

      <div className="divide-y rounded-lg border">
        <div className="flex items-center justify-between p-3">
          <div>
            <p className="text-sm font-medium">Mode</p>
            <p className="text-xs text-muted-foreground">Current firewall enforcement mode.</p>
          </div>
          <Badge variant="outline">{modeLabel(mode)}</Badge>
        </div>
        <div className="flex items-center justify-between p-3">
          <div>
            <p className="text-sm font-medium">Allowed IPs</p>
            <p className="text-xs text-muted-foreground">Total IPs on the allowlist.</p>
          </div>
          <span className="text-sm font-medium tabular-nums">{entries.length}</span>
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
          className="h-8 text-xs"
          disabled={busy}
        />
        <Button
          type="button"
          size="sm"
          className="h-8 text-xs"
          onClick={() => void handleAdd()}
          disabled={busy || !ipAddress.trim()}
        >
          Add IP
        </Button>
      </div>

      {firewallQuery.isLoading && !firewallQuery.data ? (
        <div className="py-8">
          <SpinnerBlock />
        </div>
      ) : entries.length === 0 ? (
        <EmptyState
          icon={Shield}
          title="No IPs on the allowlist"
          description="Firewall is currently in allow-all mode."
        />
      ) : (
        <div className="overflow-x-auto rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>IP Address</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="w-[96px] text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((entry) => {
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
                        onClick={() => deleteDialog.show(entry.ipAddress)}
                      >
                        Remove
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      <ConfirmDialog
        open={deleteDialog.open}
        title="Remove IP from allowlist"
        description={`${deleteDialog.data ?? ""} will no longer be allowed through the firewall.`}
        confirmLabel="Remove"
        onOpenChange={deleteDialog.onOpenChange}
        onConfirm={() => {
          if (!deleteDialog.data) {
            return;
          }
          void deleteMutation.mutateAsync(deleteDialog.data).finally(() => {
            deleteDialog.hide();
          });
        }}
      />
    </section>
  );
}
