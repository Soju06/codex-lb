import { useMemo } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { ProxyFormValues } from "@/features/accounts/components/proxy-form-state";
import { parseQuickPaste } from "@/features/accounts/components/proxy-form-state";

export type ProxyFormSectionProps = {
  values: ProxyFormValues;
  onChange: (next: ProxyFormValues) => void;
  showProxy: boolean;
  onToggleShowProxy: (next: boolean) => void;
  disabled?: boolean;
  toggleDisabled?: boolean;
  // Distinguishes input IDs between this component's instances on the
  // same page (e.g., the OAuth dialog and the import dialog could both
  // be open during navigation).
  idPrefix: string;
  // Surfaces validation/probe errors above the submit-button row that
  // owns its own copy. Pass ``null`` to suppress.
  errorMessage?: string | null;
};

export function ProxyFormSection({
  values,
  onChange,
  showProxy,
  onToggleShowProxy,
  disabled = false,
  toggleDisabled = false,
  idPrefix,
  errorMessage,
}: ProxyFormSectionProps) {
  const fieldsId = `${idPrefix}-proxy-fields`;
  const hostId = `${idPrefix}-proxy-host`;
  const portId = `${idPrefix}-proxy-port`;
  const usernameId = `${idPrefix}-proxy-username`;
  const passwordId = `${idPrefix}-proxy-password`;
  const labelId = `${idPrefix}-proxy-label`;
  const remoteDnsId = `${idPrefix}-proxy-remote-dns`;

  // Memoize the field setter so child onChange handlers are stable
  // across renders when the parent passes a fresh values object each
  // render (the common case).
  const set = useMemo(
    () =>
      <K extends keyof ProxyFormValues>(key: K, value: ProxyFormValues[K]) =>
        onChange({ ...values, [key]: value }),
    [onChange, values],
  );

  return (
    <div className="rounded-md border">
      <button
        type="button"
        className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium"
        aria-controls={fieldsId}
        aria-expanded={showProxy}
        disabled={disabled || toggleDisabled}
        onClick={() => onToggleShowProxy(!showProxy)}
      >
        <span>Configure egress proxy (optional)</span>
        <span className="text-xs text-muted-foreground">{showProxy ? "Hide" : "Show"}</span>
      </button>

      {showProxy ? (
        <div id={fieldsId} className="space-y-4 border-t px-3 py-3">
          <div className="space-y-1.5">
            <Label htmlFor={`${idPrefix}-proxy-quick`}>Quick paste</Label>
            <Input
              id={`${idPrefix}-proxy-quick`}
              autoComplete="off"
              placeholder="user:pass@host:port"
              value={values.quickPaste}
              onChange={(event) => {
                const pasted = parseQuickPaste(event.target.value);
                if (pasted) {
                  onChange({ ...values, quickPaste: event.target.value, ...pasted });
                } else {
                  set("quickPaste", event.target.value);
                }
              }}
              disabled={disabled}
            />
            <p className="text-xs text-muted-foreground">
              Paste <code>user:pass@host:port</code> to fill the fields below.
            </p>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2 space-y-1.5">
              <Label htmlFor={hostId}>Host</Label>
              <Input
                id={hostId}
                autoComplete="off"
                placeholder="proxy.example.com"
                value={values.host}
                onChange={(event) => set("host", event.target.value)}
                disabled={disabled}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={portId}>Port</Label>
              <Input
                id={portId}
                inputMode="numeric"
                pattern="\d*"
                placeholder="1080"
                value={values.portText}
                onChange={(event) => set("portText", event.target.value)}
                disabled={disabled}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor={usernameId}>Username (optional)</Label>
              <Input
                id={usernameId}
                autoComplete="off"
                value={values.username}
                onChange={(event) => set("username", event.target.value)}
                disabled={disabled}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={passwordId}>Password (optional)</Label>
              <Input
                id={passwordId}
                type="password"
                autoComplete="new-password"
                placeholder="Optional"
                value={values.password}
                onChange={(event) => set("password", event.target.value)}
                disabled={disabled}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor={labelId}>Label (optional)</Label>
            <Input
              id={labelId}
              placeholder="house-1"
              value={values.label}
              onChange={(event) => set("label", event.target.value)}
              disabled={disabled}
            />
          </div>

          <div className="flex items-center justify-between rounded-md border px-3 py-2">
            <div>
              <Label htmlFor={remoteDnsId} className="text-sm">
                Resolve hostnames at the proxy
              </Label>
              <p className="text-xs text-muted-foreground">
                Recommended. Uses <code>socks5h://</code> so the proxy resolves DNS and prevents DNS
                leaks to the local network.
              </p>
            </div>
            <Switch
              id={remoteDnsId}
              checked={values.remoteDns}
              onCheckedChange={(next) => set("remoteDns", next)}
              disabled={disabled}
            />
          </div>

          {errorMessage ? (
            <AlertMessage variant="error">{errorMessage}</AlertMessage>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
