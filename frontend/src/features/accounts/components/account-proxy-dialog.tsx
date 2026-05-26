import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useSetAccountProxy } from "@/features/accounts/hooks/use-accounts";
import { formatProbeError } from "@/features/accounts/proxy-errors";
import {
  AccountProxyInputSchema,
  type AccountProxySummary,
} from "@/features/accounts/schemas";

type PasswordMode = "keep" | "replace" | "clear";

export type AccountProxyDialogProps = {
  open: boolean;
  accountId: string | null;
  /** Existing proxy summary, if any — populates fields and switches the dialog
   * to "edit" mode. The password is never returned by the API; an existing
   * configuration shows a "(unchanged)" placeholder until the operator types
   * a replacement. */
  existing: AccountProxySummary | null;
  onOpenChange: (open: boolean) => void;
};

export function AccountProxyDialog({
  open,
  accountId,
  existing,
  onOpenChange,
}: AccountProxyDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{existing ? "Edit egress proxy" : "Configure egress proxy"}</DialogTitle>
          <DialogDescription>
            All outbound traffic for this account will be routed through this SOCKS5 proxy.
            The configuration is validated by attempting a real OAuth refresh through it before
            it is saved.
          </DialogDescription>
        </DialogHeader>
        {/* Remounting the form whenever the dialog (re-)opens guarantees a
            clean slate even if the operator cancels mid-edit and reopens the
            dialog later. The remount also avoids the eslint
            ``react-hooks/set-state-in-effect`` warning that fires when state
            is reset synchronously inside an effect. */}
        <AccountProxyForm
          key={open ? `open-${existing?.host ?? "new"}` : "closed"}
          accountId={accountId}
          existing={existing}
          onSubmitted={() => onOpenChange(false)}
          onCancel={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

type AccountProxyFormProps = {
  accountId: string | null;
  existing: AccountProxySummary | null;
  onSubmitted: () => void;
  onCancel: () => void;
};

function AccountProxyForm({
  accountId,
  existing,
  onSubmitted,
  onCancel,
}: AccountProxyFormProps) {
  const setProxy = useSetAccountProxy();

  const [host, setHost] = useState(() => existing?.host ?? "");
  const [portText, setPortText] = useState(() => (existing ? String(existing.port) : "1080"));
  const [username, setUsername] = useState(() => existing?.username ?? "");
  const [password, setPassword] = useState("");
  const [passwordMode, setPasswordMode] = useState<PasswordMode>(() =>
    existing?.hasPassword ? "keep" : "replace",
  );
  const [remoteDns, setRemoteDns] = useState(() => existing?.remoteDns ?? true);
  const [label, setLabel] = useState(() => existing?.label ?? "");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const portValue = useMemo(() => {
    const trimmed = portText.trim();
    if (!trimmed) return Number.NaN;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) && Number.isInteger(parsed) ? parsed : Number.NaN;
  }, [portText]);

  const localValidation = useMemo(() => {
    const payload = {
      host,
      port: portValue,
      username: username || undefined,
      password:
        passwordMode === "replace" && password.trim() ? password : undefined,
      clearPassword: passwordMode === "clear",
      remoteDns,
      label: label || undefined,
    };
    return AccountProxyInputSchema.safeParse(payload);
  }, [host, portValue, username, password, passwordMode, remoteDns, label]);

  const validationError = localValidation.success
    ? null
    : localValidation.error.issues[0]?.message ?? "Invalid input";

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!accountId) return;
    if (!localValidation.success) {
      setErrorMessage(validationError);
      return;
    }
    setErrorMessage(null);
    try {
      await setProxy.mutateAsync({ accountId, payload: localValidation.data });
      onSubmitted();
    } catch (error) {
      setErrorMessage(formatProbeError(error));
    }
  };

  const submitting = setProxy.isPending;
  const passwordPlaceholder = existing?.hasPassword ? "Replacement password" : "Optional";
  const passwordDisabled = submitting || passwordMode !== "replace";

  return (
    <form className="space-y-4" onSubmit={handleSubmit}>
      <div className="grid grid-cols-3 gap-3">
        <div className="col-span-2 space-y-1.5">
          <Label htmlFor="proxy-host">Host</Label>
          <Input
            id="proxy-host"
            autoComplete="off"
            placeholder="proxy.example.com"
            value={host}
            onChange={(event) => setHost(event.target.value)}
            disabled={submitting}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="proxy-port">Port</Label>
          <Input
            id="proxy-port"
            inputMode="numeric"
            pattern="\\d*"
            placeholder="1080"
            value={portText}
            onChange={(event) => setPortText(event.target.value)}
            disabled={submitting}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="proxy-username">Username (optional)</Label>
          <Input
            id="proxy-username"
            autoComplete="off"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            disabled={submitting}
          />
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="proxy-password">Password</Label>
            {existing?.hasPassword ? (
              <select
                aria-label="Password mode"
                className="rounded border bg-background px-2 py-1 text-xs"
                value={passwordMode}
                onChange={(event) => setPasswordMode(event.target.value as PasswordMode)}
                disabled={submitting}
              >
                <option value="keep">Keep</option>
                <option value="replace">Replace</option>
                <option value="clear">Clear</option>
              </select>
            ) : null}
          </div>
          <Input
            id="proxy-password"
            type="password"
            autoComplete="new-password"
            placeholder={passwordPlaceholder}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={passwordDisabled}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="proxy-label">Label (optional)</Label>
        <Input
          id="proxy-label"
          placeholder="house-1"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          disabled={submitting}
        />
      </div>

      <div className="flex items-center justify-between rounded-md border px-3 py-2">
        <div>
          <Label htmlFor="proxy-remote-dns" className="text-sm">
            Resolve hostnames at the proxy
          </Label>
          <p className="text-xs text-muted-foreground">
            Recommended. Uses <code>socks5h://</code> so the proxy resolves DNS — prevents DNS
            leaks to the local network.
          </p>
        </div>
        <Switch
          id="proxy-remote-dns"
          checked={remoteDns}
          onCheckedChange={setRemoteDns}
          disabled={submitting}
        />
      </div>

      {errorMessage ? <AlertMessage variant="error">{errorMessage}</AlertMessage> : null}

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
          Cancel
        </Button>
        <Button type="submit" disabled={submitting || !accountId}>
          {submitting ? "Validating…" : existing ? "Save" : "Validate & save"}
        </Button>
      </DialogFooter>
    </form>
  );
}
