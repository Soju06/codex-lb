import { Eye } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { removeGuestPassword, setGuestPassword } from "@/features/auth/api";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";
import { getErrorMessage } from "@/utils/errors";

export type GuestAccessSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
  onRefresh: () => Promise<unknown>;
};

export function GuestAccessSettings({
  settings,
  busy,
  onSave,
  onRefresh,
}: GuestAccessSettingsProps) {
  const [password, setPassword] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const disabled = busy || passwordBusy;

  const save = (patch: Partial<SettingsUpdateRequest>) =>
    void onSave(buildSettingsUpdateRequest(settings, patch));

  const handleSetPassword = async () => {
    setError(null);
    setPasswordBusy(true);
    try {
      await setGuestPassword({ password });
      setPassword("");
      await onRefresh();
      toast.success("Guest password saved");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setPasswordBusy(false);
    }
  };

  const handleRemovePassword = async () => {
    setError(null);
    setPasswordBusy(true);
    try {
      await removeGuestPassword();
      await onRefresh();
      toast.success("Guest password removed");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setPasswordBusy(false);
    }
  };

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <Eye className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">Guest access</h3>
              <p className="text-xs text-muted-foreground">
                Share read-only dashboard visibility without admin controls.
              </p>
            </div>
          </div>
          <Switch
            checked={settings.guestAccessEnabled}
            disabled={disabled}
            onCheckedChange={(checked) => save({ guestAccessEnabled: checked })}
          />
        </div>

        {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

        <div className="flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-medium">Guest password</p>
            <p className="text-xs text-muted-foreground">
              {settings.guestPasswordConfigured
                ? "Guest viewers must sign in with the guest password."
                : "Leave unset to allow passwordless read-only guest access when enabled."}
            </p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Input
              type="password"
              autoComplete="new-password"
              value={password}
              disabled={disabled}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Optional guest password"
              className="h-8 text-xs sm:w-56"
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8 text-xs"
              disabled={disabled || !password.trim()}
              onClick={() => void handleSetPassword()}
            >
              Save
            </Button>
            {settings.guestPasswordConfigured ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs text-destructive hover:text-destructive"
                disabled={disabled}
                onClick={() => void handleRemovePassword()}
              >
                Remove
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
