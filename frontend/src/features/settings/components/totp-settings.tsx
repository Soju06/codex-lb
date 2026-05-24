import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Shield } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { z } from "zod";

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
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { InputOTP, InputOTPGroup, InputOTPSeparator, InputOTPSlot } from "@/components/ui/input-otp";
import { Switch } from "@/components/ui/switch";
import {
  confirmTotpSetup,
  disableTotp,
  startTotpSetup,
} from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { buildSettingsUpdateRequest } from "@/features/settings/payload";
import type { DashboardSettings, SettingsUpdateRequest } from "@/features/settings/schemas";
import { getErrorMessage } from "@/utils/errors";

// NOTE: validation message is intentionally a translation key resolved at render time;
// keeping the schema decoupled from the i18n instance lets us continue to colocate it here.
const totpCodeSchema = z.object({
  code: z.string().length(6, "settings.totp.validation.codeLength"),
});

type TotpCodeValues = z.infer<typeof totpCodeSchema>;
type TotpDialog = "setup" | "disable" | null;

export type TotpSettingsProps = {
  settings: DashboardSettings;
  disabled?: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function TotpSettings({ settings, disabled = false, onSave }: TotpSettingsProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const refreshSession = useAuthStore((state) => state.refreshSession);

  const [activeDialog, setActiveDialog] = useState<TotpDialog>(null);
  const [setupSecret, setSetupSecret] = useState<string | null>(null);
  const [setupQrDataUri, setSetupQrDataUri] = useState<string | null>(null);
  const [prefetching, setPrefetching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirmForm = useForm<TotpCodeValues>({
    resolver: zodResolver(totpCodeSchema),
    defaultValues: { code: "" },
  });

  const disableForm = useForm<TotpCodeValues>({
    resolver: zodResolver(totpCodeSchema),
    defaultValues: { code: "" },
  });

  const lock = disabled || prefetching || confirmForm.formState.isSubmitting || disableForm.formState.isSubmitting;

  const closeDialog = () => {
    setActiveDialog(null);
    setError(null);
    setSetupSecret(null);
    setSetupQrDataUri(null);
    confirmForm.reset();
    disableForm.reset();
  };

  const handleOpenSetup = async () => {
    setActiveDialog("setup");
    setPrefetching(true);
    setError(null);
    try {
      const response = await startTotpSetup();
      setSetupSecret(response.secret);
      setSetupQrDataUri(response.qrSvgDataUri);
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setPrefetching(false);
    }
  };

  const handleConfirmSetup = async (values: TotpCodeValues) => {
    if (!setupSecret) return;
    setError(null);
    try {
      await confirmTotpSetup({ secret: setupSecret, code: values.code });
      await refreshSession();
      void queryClient.invalidateQueries({ queryKey: ["settings", "detail"] });
      toast.success(t("settings.totp.toasts.configured"));
      closeDialog();
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  const handleDisable = async (values: TotpCodeValues) => {
    setError(null);
    try {
      await disableTotp({ code: values.code });
      await refreshSession();
      void queryClient.invalidateQueries({ queryKey: ["settings", "detail"] });
      toast.success(t("settings.totp.toasts.disabled"));
      closeDialog();
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        {/* Status row */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <Shield className="h-4 w-4 text-primary" aria-hidden="true" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">{t("settings.totp.title")}</h3>
              <p className="text-xs text-muted-foreground">
                {settings.totpConfigured
                  ? t("settings.totp.status.configured")
                  : t("settings.totp.status.notConfigured")}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {settings.totpConfigured ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs text-destructive hover:text-destructive"
                disabled={lock}
                onClick={() => setActiveDialog("disable")}
              >
                {t("settings.totp.actions.disable")}
              </Button>
            ) : (
              <Button
                type="button"
                size="sm"
                className="h-8 text-xs"
                disabled={lock}
                onClick={handleOpenSetup}
              >
                {t("settings.totp.actions.enable")}
              </Button>
            )}
          </div>
        </div>

        {/* Require on login toggle */}
        <div className="flex items-center justify-between rounded-lg border p-3">
          <div>
            <p className="text-sm font-medium">{t("settings.totp.requireLogin.label")}</p>
            <p className="text-xs text-muted-foreground">{t("settings.totp.requireLogin.description")}</p>
          </div>
          <Switch
            checked={settings.totpRequiredOnLogin}
            disabled={lock}
            onCheckedChange={(checked) =>
              void onSave(buildSettingsUpdateRequest(settings, { totpRequiredOnLogin: checked }))
            }
          />
        </div>
      </div>

      {/* Setup dialog */}
      <Dialog open={activeDialog === "setup"} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("settings.totp.setupDialog.title")}</DialogTitle>
            <DialogDescription>
              {t("settings.totp.setupDialog.description")}
            </DialogDescription>
          </DialogHeader>
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

          {setupQrDataUri ? (
            <div className="flex justify-center rounded-lg border bg-card p-4 dark:bg-white/95">
              <img src={setupQrDataUri} alt={t("settings.totp.setupDialog.qrAlt")} className="h-40 w-40" />
            </div>
          ) : null}

          {setupSecret ? (
            <p className="rounded-lg border bg-muted/30 px-3 py-2 font-mono text-xs">
              {t("settings.totp.setupDialog.secretLabel")} {setupSecret}
            </p>
          ) : null}

          {setupSecret ? (
            <Form {...confirmForm}>
              <form onSubmit={confirmForm.handleSubmit(handleConfirmSetup)} className="space-y-4">
                <FormField
                  control={confirmForm.control}
                  name="code"
                  render={({ field, fieldState }) => (
                    <FormItem className="flex flex-col items-center gap-2">
                      <FormLabel className="sr-only">{t("settings.totp.setupDialog.codeLabel")}</FormLabel>
                      <FormControl>
                        <InputOTP
                          maxLength={6}
                          value={field.value}
                          onChange={field.onChange}
                        >
                          <InputOTPGroup>
                            <InputOTPSlot index={0} />
                            <InputOTPSlot index={1} />
                            <InputOTPSlot index={2} />
                          </InputOTPGroup>
                          <InputOTPSeparator />
                          <InputOTPGroup>
                            <InputOTPSlot index={3} />
                            <InputOTPSlot index={4} />
                            <InputOTPSlot index={5} />
                          </InputOTPGroup>
                        </InputOTP>
                      </FormControl>
                      {fieldState.error?.message ? (
                        <FormMessage>{t(fieldState.error.message)}</FormMessage>
                      ) : (
                        <FormMessage />
                      )}
                    </FormItem>
                  )}
                />
                <DialogFooter>
                  <Button type="button" variant="outline" onClick={closeDialog} disabled={prefetching}>
                    {t("common.cancel")}
                  </Button>
                  <Button type="submit" disabled={lock}>
                    {t("settings.totp.setupDialog.submit")}
                  </Button>
                </DialogFooter>
              </form>
            </Form>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* Disable dialog */}
      <Dialog open={activeDialog === "disable"} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("settings.totp.disableDialog.title")}</DialogTitle>
            <DialogDescription>{t("settings.totp.disableDialog.description")}</DialogDescription>
          </DialogHeader>
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
          <Form {...disableForm}>
            <form onSubmit={disableForm.handleSubmit(handleDisable)} className="space-y-4">
              <FormField
                control={disableForm.control}
                name="code"
                render={({ field, fieldState }) => (
                  <FormItem className="flex flex-col items-center gap-2">
                    <FormLabel className="sr-only">{t("settings.totp.disableDialog.codeLabel")}</FormLabel>
                    <FormControl>
                      <InputOTP
                        maxLength={6}
                        value={field.value}
                        onChange={field.onChange}
                      >
                        <InputOTPGroup>
                          <InputOTPSlot index={0} />
                          <InputOTPSlot index={1} />
                          <InputOTPSlot index={2} />
                        </InputOTPGroup>
                        <InputOTPSeparator />
                        <InputOTPGroup>
                          <InputOTPSlot index={3} />
                          <InputOTPSlot index={4} />
                          <InputOTPSlot index={5} />
                        </InputOTPGroup>
                      </InputOTP>
                    </FormControl>
                    {fieldState.error?.message ? (
                      <FormMessage>{t(fieldState.error.message)}</FormMessage>
                    ) : (
                      <FormMessage />
                    )}
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={closeDialog} disabled={lock}>
                  {t("common.cancel")}
                </Button>
                <Button type="submit" variant="destructive" disabled={lock}>
                  {t("settings.totp.disableDialog.submit")}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>
    </section>
  );
}
