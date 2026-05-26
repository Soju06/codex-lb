import { useState } from "react";
import { Globe, KeyRound, Network, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { AccountProxyDialog } from "@/features/accounts/components/account-proxy-dialog";
import { useClearAccountProxy } from "@/features/accounts/hooks/use-accounts";
import type { AccountSummary } from "@/features/accounts/schemas";

export type AccountProxySectionProps = {
  account: AccountSummary;
};

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

export function AccountProxySection({ account }: AccountProxySectionProps) {
  const [editorOpen, setEditorOpen] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const clearProxy = useClearAccountProxy();

  const proxy = account.proxy ?? null;

  return (
    <section
      aria-label="Network egress"
      className="space-y-2 border-t pt-4"
      data-testid="account-proxy-section"
    >
      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <Network className="h-3.5 w-3.5" /> Network egress
        </h3>
        {proxy ? (
          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 gap-1.5 text-xs"
              onClick={() => setEditorOpen(true)}
              disabled={clearProxy.isPending}
            >
              Edit proxy
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 gap-1.5 text-xs text-destructive hover:text-destructive"
              onClick={() => setConfirmRemove(true)}
              disabled={clearProxy.isPending}
            >
              <Trash2 className="h-3.5 w-3.5" />
              Remove
            </Button>
          </div>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 gap-1.5 text-xs"
            onClick={() => setEditorOpen(true)}
          >
            Configure proxy
          </Button>
        )}
      </div>

      {proxy ? (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted-foreground">Endpoint</dt>
          <dd className="font-mono">
            socks5{proxy.remoteDns ? "h" : ""}://
            {proxy.username ? `${proxy.username}@` : ""}
            {proxy.host}:{proxy.port}
          </dd>
          <dt className="text-muted-foreground">DNS</dt>
          <dd>
            <span className="inline-flex items-center gap-1">
              <Globe className="h-3 w-3" />
              {proxy.remoteDns ? "Resolved at proxy (recommended)" : "Resolved locally"}
            </span>
          </dd>
          <dt className="text-muted-foreground">Auth</dt>
          <dd>
            <span className="inline-flex items-center gap-1">
              <KeyRound className="h-3 w-3" />
              {proxy.hasPassword ? "Password configured" : "No password"}
            </span>
          </dd>
          {proxy.label ? (
            <>
              <dt className="text-muted-foreground">Label</dt>
              <dd>{proxy.label}</dd>
            </>
          ) : null}
          <dt className="text-muted-foreground">Last validated</dt>
          <dd>{formatTimestamp(proxy.lastValidatedAt)}</dd>
        </dl>
      ) : (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted-foreground">Endpoint</dt>
          <dd>Direct egress</dd>
        </dl>
      )}

      <AccountProxyDialog
        open={editorOpen}
        onOpenChange={setEditorOpen}
        accountId={account.accountId}
        existing={proxy}
      />

      <ConfirmDialog
        open={confirmRemove}
        title="Remove egress proxy?"
        description="The account will go back to direct egress on the next request."
        onOpenChange={setConfirmRemove}
        onConfirm={async () => {
          try {
            await clearProxy.mutateAsync(account.accountId);
          } finally {
            setConfirmRemove(false);
          }
        }}
        confirmLabel={clearProxy.isPending ? "Removing…" : "Remove proxy"}
      />
    </section>
  );
}
