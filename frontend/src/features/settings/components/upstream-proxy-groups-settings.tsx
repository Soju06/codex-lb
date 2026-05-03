import { useState } from "react";
import { Network } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { UpstreamProxyGroup } from "@/features/settings/schemas";

export type UpstreamProxyGroupsSettingsProps = {
  groups: UpstreamProxyGroup[];
  busy: boolean;
  onSave: (name: string, proxyUrl: string) => void;
  onDelete: (name: string) => void;
};

export function UpstreamProxyGroupsSettings({
  groups,
  busy,
  onSave,
  onDelete,
}: UpstreamProxyGroupsSettingsProps) {
  const [name, setName] = useState("");
  const [proxyUrl, setProxyUrl] = useState("");
  const canSave = name.trim().length > 0 && proxyUrl.trim().length > 0;

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <Network className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">Proxy groups</h3>
            <p className="text-xs text-muted-foreground">
              Create reusable upstream proxies and assign accounts to a group.
            </p>
          </div>
        </div>

        <div className="space-y-3 rounded-lg border p-3">
          <div className="grid gap-2 sm:grid-cols-[12rem_minmax(0,1fr)_auto]">
            <Input
              value={name}
              disabled={busy}
              placeholder="group name"
              onChange={(event) => setName(event.target.value)}
              className="h-8 text-xs"
            />
            <Input
              value={proxyUrl}
              disabled={busy}
              placeholder="http://user:pass@host:port"
              onChange={(event) => setProxyUrl(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && canSave) {
                  onSave(name.trim(), proxyUrl.trim());
                  setProxyUrl("");
                }
              }}
              className="h-8 text-xs"
            />
            <Button
              type="button"
              size="sm"
              className="h-8 text-xs"
              disabled={busy || !canSave}
              onClick={() => {
                onSave(name.trim(), proxyUrl.trim());
                setProxyUrl("");
              }}
            >
              Save group
            </Button>
          </div>

          {groups.length === 0 ? (
            <p className="text-xs text-muted-foreground">No proxy groups configured.</p>
          ) : (
            <div className="divide-y rounded-md border">
              {groups.map((group) => (
                <div key={group.name} className="flex items-center justify-between gap-3 p-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{group.name}</p>
                    <p className="truncate text-xs text-muted-foreground">{group.proxyUrl}</p>
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-8 text-xs"
                    disabled={busy}
                    onClick={() => onDelete(group.name)}
                  >
                    Delete
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
