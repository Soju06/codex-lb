import { useState } from "react";
import { Network } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { AccountSummary } from "@/features/accounts/schemas";

export type AccountProxySettingsProps = {
  account: AccountSummary;
  busy: boolean;
  onSave: (payload: { upstreamProxyUrl?: string | null; upstreamProxyGroup?: string | null }) => void;
};

export function AccountProxySettings({ account, busy, onSave }: AccountProxySettingsProps) {
  const currentProxyUrl = account.upstreamProxy?.proxyUrl ?? "";
  const currentProxyGroup = account.upstreamProxy?.proxyGroup ?? "";

  return (
    <AccountProxySettingsFields
      key={`${account.accountId}:${currentProxyUrl}:${currentProxyGroup}`}
      account={account}
      busy={busy}
      currentProxyGroup={currentProxyGroup}
      currentProxyUrl={currentProxyUrl}
      onSave={onSave}
    />
  );
}

type AccountProxySettingsFieldsProps = AccountProxySettingsProps & {
  currentProxyGroup: string;
  currentProxyUrl: string;
};

function AccountProxySettingsFields({
  account,
  busy,
  currentProxyGroup,
  currentProxyUrl,
  onSave,
}: AccountProxySettingsFieldsProps) {
  const [proxyUrl, setProxyUrl] = useState(currentProxyUrl);
  const [proxyGroup, setProxyGroup] = useState(currentProxyGroup);
  const proxyUrlChanged = proxyUrl.trim() !== currentProxyUrl;
  const proxyGroupChanged = proxyGroup.trim() !== currentProxyGroup;

  return (
    <section className="rounded-lg border p-4">
      <div className="mb-3 flex items-center gap-2">
        <Network className="h-4 w-4 text-primary" aria-hidden="true" />
        <div>
          <h3 className="text-sm font-semibold">Account proxy</h3>
          <p className="text-xs text-muted-foreground">
            Overrides the global proxy. If URL is empty, this account can inherit its group or global proxy.
          </p>
        </div>
      </div>

      <div className="space-y-3">
        <div className="grid gap-2 sm:grid-cols-[8rem_minmax(0,1fr)_auto] sm:items-center">
          <p className="text-xs font-medium text-muted-foreground">Proxy URL</p>
          <Input
            value={proxyUrl}
            disabled={busy}
            placeholder="http://user:pass@host:port"
            onChange={(event) => setProxyUrl(event.target.value)}
            className="h-8 text-xs"
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 text-xs"
            disabled={busy || !proxyUrlChanged}
            onClick={() => onSave({ upstreamProxyUrl: proxyUrl.trim() || null })}
          >
            Save URL
          </Button>
        </div>

        <div className="grid gap-2 sm:grid-cols-[8rem_minmax(0,1fr)_auto] sm:items-center">
          <p className="text-xs font-medium text-muted-foreground">Proxy group</p>
          <Input
            value={proxyGroup}
            disabled={busy}
            placeholder="group name"
            onChange={(event) => setProxyGroup(event.target.value)}
            className="h-8 text-xs"
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 text-xs"
            disabled={busy || !proxyGroupChanged}
            onClick={() => onSave({ upstreamProxyGroup: proxyGroup.trim() || null })}
          >
            Save group
          </Button>
        </div>

        {account.upstreamProxy?.configured ? (
          <p className="text-xs text-muted-foreground">
            Effective account binding is configured. Saved credentials are hidden after saving.
          </p>
        ) : null}
      </div>
    </section>
  );
}
