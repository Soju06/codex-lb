import { Eye } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { removeGuestPassword, setGuestPassword } from "@/features/auth/api";
import { getFirstZodIssueMessage } from "@/features/auth/schemas";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";
import { getErrorMessage } from "@/utils/errors";

export type GuestAccessSettingsProps = {
  settings: DashboardSettings;
  busy: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
  onRefresh: () => Promise<unknown>;
};

function resolveGuestAccessErrorMessage(
  caught: unknown,
  t: ReturnType<typeof useTranslation>["t"],
): string {
  const zodIssueMessage = getFirstZodIssueMessage(caught);
  if (zodIssueMessage) {
    return t(zodIssueMessage, { defaultValue: zodIssueMessage });
  }

  const message = getErrorMessage(caught);
  return t(message, { defaultValue: message });
}

export function GuestAccessSettings({
  settings,
  busy,
  onSave,
  onRefresh,
}: GuestAccessSettingsProps) {
  const { t } = useTranslation();
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
      toast.success(t("settings.guestAccess.toasts.passwordSaved"));
    } catch (caught) {
      setError(resolveGuestAccessErrorMessage(caught, t));
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
      toast.success(t("settings.guestAccess.toasts.passwordRemoved"));
    } catch (caught) {
      setError(resolveGuestAccessErrorMessage(caught, t));
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
              <h3 className="text-sm font-semibold">{t("settings.guestAccess.title")}</h3>
              <p className="text-xs text-muted-foreground">
                {t("settings.guestAccess.description")}
              </p>
            </div>
          </div>
          <Switch
            aria-label={t("settings.guestAccess.toggleAria")}
            checked={settings.guestAccessEnabled}
            disabled={disabled}
            onCheckedChange={(checked) => save({ guestAccessEnabled: checked })}
          />
        </div>

        {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

        <div className="flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-medium">{t("settings.guestAccess.password.label")}</p>
            <p className="text-xs text-muted-foreground">
              {settings.guestPasswordConfigured
                ? t("settings.guestAccess.password.configuredDescription")
                : t("settings.guestAccess.password.emptyDescription")}
            </p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Input
              type="password"
              autoComplete="new-password"
              value={password}
              disabled={disabled}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={t("settings.guestAccess.password.placeholder")}
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
              {t("settings.guestAccess.password.save")}
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
                {t("settings.guestAccess.password.remove")}
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
