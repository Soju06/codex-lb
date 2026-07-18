import { useMemo, useState } from "react";
import { Shield } from "lucide-react";
import { useTranslation } from "react-i18next";

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

function modeLabel(mode: "allow_all" | "allowlist_active", t: ReturnType<typeof useTranslation>["t"]): string {
  return mode === "allow_all" ? t("firewall.modes.allowAll") : t("firewall.modes.allowlistActive");
}

export type FirewallSectionProps = {
  disabled?: boolean;
};

export function FirewallSection({ disabled = false }: FirewallSectionProps) {
  const { t } = useTranslation();
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
  const busy = disabled || createMutation.isPending || deleteMutation.isPending;

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
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <Shield className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <div>
	          <h3 className="text-sm font-semibold">{t("firewall.title")}</h3>
	          <p className="text-xs text-muted-foreground">{t("firewall.description")}</p>
        </div>
      </div>

      {mutationError ? <AlertMessage variant="error">{mutationError}</AlertMessage> : null}

      <div className="flex items-center gap-3 rounded-lg border px-3 py-2">
        <div className="flex items-center gap-1.5">
	          <span className="text-xs text-muted-foreground">{t("firewall.mode")}</span>
	          <Badge variant="outline">{modeLabel(mode, t)}</Badge>
        </div>
        <div className="h-4 w-px bg-border" />
        <div className="flex items-center gap-1.5">
	          <span className="text-xs text-muted-foreground">{t("firewall.allowedIps")}</span>
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
	          {t("firewall.actions.addIp")}
        </Button>
      </div>

      {firewallQuery.isLoading && !firewallQuery.data ? (
        <div className="py-8">
          <SpinnerBlock />
        </div>
      ) : entries.length === 0 ? (
	        <EmptyState
	          icon={Shield}
	          title={t("firewall.empty.title")}
	          description={t("firewall.empty.description")}
	        />
      ) : (
        <div className="overflow-x-auto rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
	                <TableHead>{t("firewall.table.ipAddress")}</TableHead>
	                <TableHead>{t("firewall.table.created")}</TableHead>
	                <TableHead className="w-[96px] text-right">{t("apiKeys.table.actions")}</TableHead>
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
	                        {t("common.actions.remove")}
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
	        title={t("firewall.removeDialog.title")}
	        description={t("firewall.removeDialog.description", { ip: deleteDialog.data ?? "" })}
	        confirmLabel={t("common.actions.remove")}
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
