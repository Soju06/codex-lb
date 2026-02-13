import { useState } from "react";
import type { FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  confirmTotpSetup,
  disableTotp,
  startTotpSetup,
} from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "TOTP request failed";
}

export type TotpSettingsProps = {
  settings: DashboardSettings;
  disabled?: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function TotpSettings({ settings, disabled = false, onSave }: TotpSettingsProps) {
  const refreshSession = useAuthStore((state) => state.refreshSession);

  const [setupSecret, setSetupSecret] = useState<string | null>(null);
  const [setupQrDataUri, setSetupQrDataUri] = useState<string | null>(null);
  const [setupCode, setSetupCode] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const lock = disabled || busy;

  const handleStartSetup = async () => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const response = await startTotpSetup();
      setSetupSecret(response.secret);
      setSetupQrDataUri(response.qrSvgDataUri);
      setMessage("Scan the QR code and enter the verification code.");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleConfirmSetup = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!setupSecret) {
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await confirmTotpSetup({ secret: setupSecret, code: setupCode });
      await refreshSession();
      setMessage("TOTP configured.");
      setSetupSecret(null);
      setSetupQrDataUri(null);
      setSetupCode("");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleDisable = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await disableTotp({ code: disableCode });
      await refreshSession();
      setDisableCode("");
      setMessage("TOTP disabled.");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-3 rounded-xl border p-4">
      <div>
        <h3 className="text-sm font-semibold">TOTP</h3>
        <p className="text-xs text-muted-foreground">Manage one-time password verification for dashboard login.</p>
      </div>

      {message ? <p className="rounded-md bg-emerald-500/10 px-2 py-1 text-xs text-emerald-700">{message}</p> : null}
      {error ? <p className="rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p> : null}

      <div className="flex items-center justify-between rounded-md border p-2">
        <div>
          <p className="text-sm">Require TOTP on login</p>
          <p className="text-xs text-muted-foreground">Prompt for TOTP after password login.</p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={lock}
          onClick={() =>
            void onSave({
              stickyThreadsEnabled: settings.stickyThreadsEnabled,
              preferEarlierResetAccounts: settings.preferEarlierResetAccounts,
              totpRequiredOnLogin: !settings.totpRequiredOnLogin,
              apiKeyAuthEnabled: settings.apiKeyAuthEnabled,
            })
          }
        >
          {settings.totpRequiredOnLogin ? "Disable" : "Enable"}
        </Button>
      </div>

      {!settings.totpConfigured ? (
        <div className="space-y-2 rounded-md border p-3">
          <Button type="button" size="sm" onClick={handleStartSetup} disabled={lock}>
            Start setup
          </Button>
          {setupQrDataUri ? <img src={setupQrDataUri} alt="TOTP QR code" className="h-40 w-40" /> : null}
          {setupSecret ? <p className="font-mono text-xs">Secret: {setupSecret}</p> : null}

          {setupSecret ? (
            <form className="space-y-2" onSubmit={handleConfirmSetup}>
              <Label htmlFor="totp-setup-code">Verification code</Label>
              <Input
                id="totp-setup-code"
                inputMode="numeric"
                maxLength={6}
                value={setupCode}
                onChange={(event) => setSetupCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
              />
              <Button type="submit" size="sm" disabled={lock || setupCode.length !== 6}>
                Confirm setup
              </Button>
            </form>
          ) : null}
        </div>
      ) : (
        <form className="space-y-2 rounded-md border p-3" onSubmit={handleDisable}>
          <Label htmlFor="totp-disable-code">Disable with TOTP code</Label>
          <Input
            id="totp-disable-code"
            inputMode="numeric"
            maxLength={6}
            value={disableCode}
            onChange={(event) => setDisableCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
          />
          <Button type="submit" size="sm" variant="destructive" disabled={lock || disableCode.length !== 6}>
            Disable TOTP
          </Button>
        </form>
      )}
    </section>
  );
}
