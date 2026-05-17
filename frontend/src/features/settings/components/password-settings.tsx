import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRound } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

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
import { Input } from "@/components/ui/input";
import { changePassword, loginPassword, removePassword, setupPassword, verifyTotp } from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import {
  PasswordChangeRequestSchema,
  PasswordRemoveRequestSchema,
  PasswordSetupRequestSchema,
  TotpVerifyRequestSchema,
} from "@/features/auth/schemas";
import { getErrorMessage } from "@/utils/errors";

type PasswordDialog = "setup" | "change" | "remove" | "verify" | null;

export type PasswordSettingsProps = {
  disabled?: boolean;
};

export function PasswordSettings({ disabled = false }: PasswordSettingsProps) {
  const { t } = useTranslation();
  const passwordRequired = useAuthStore((s) => s.passwordRequired);
  const bootstrapRequired = useAuthStore((s) => s.bootstrapRequired);
  const bootstrapTokenConfigured = useAuthStore((s) => s.bootstrapTokenConfigured);
  const authMode = useAuthStore((s) => s.authMode);
  const passwordManagementEnabled = useAuthStore((s) => s.passwordManagementEnabled);
  const passwordSessionActive = useAuthStore((s) => s.passwordSessionActive);
  const refreshSession = useAuthStore((s) => s.refreshSession);

  const authenticated = useAuthStore((s) => s.authenticated);
  const [activeDialog, setActiveDialog] = useState<PasswordDialog>(null);
  const [verifyStep, setVerifyStep] = useState<"password" | "totp">("password");
  const [error, setError] = useState<string | null>(null);

  const setupForm = useForm({
    resolver: zodResolver(PasswordSetupRequestSchema),
    defaultValues: { password: "", bootstrapToken: "" },
  });

  const changeForm = useForm({
    resolver: zodResolver(PasswordChangeRequestSchema),
    defaultValues: { currentPassword: "", newPassword: "" },
  });

  const removeForm = useForm({
    resolver: zodResolver(PasswordRemoveRequestSchema),
    defaultValues: { password: "" },
  });

  const verifyForm = useForm({
    resolver: zodResolver(PasswordRemoveRequestSchema),
    defaultValues: { password: "" },
  });

  const verifyTotpForm = useForm({
    resolver: zodResolver(TotpVerifyRequestSchema),
    defaultValues: { code: "" },
  });

  const busy =
    setupForm.formState.isSubmitting ||
    changeForm.formState.isSubmitting ||
    removeForm.formState.isSubmitting ||
    verifyForm.formState.isSubmitting ||
    verifyTotpForm.formState.isSubmitting;
  const lock = busy || disabled || !passwordManagementEnabled;

  const closeDialog = () => {
    setActiveDialog(null);
    setError(null);
    setupForm.reset();
    changeForm.reset();
    removeForm.reset();
    verifyForm.reset();
    verifyTotpForm.reset();
    setVerifyStep("password");
  };

  const handleSetup = async (values: { password: string; bootstrapToken?: string }) => {
    setError(null);
    try {
      await setupPassword({
        password: values.password,
        bootstrapToken: values.bootstrapToken?.trim() ? values.bootstrapToken.trim() : undefined,
      });
      await refreshSession();
      toast.success(t("settings.password.toasts.configured"));
      closeDialog();
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  const handleChange = async (values: { currentPassword: string; newPassword: string }) => {
    setError(null);
    try {
      await changePassword(values);
      toast.success(t("settings.password.toasts.changed"));
      closeDialog();
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  const handleRemove = async (values: { password: string }) => {
    setError(null);
    try {
      await removePassword(values);
      await refreshSession();
      toast.success(t("settings.password.toasts.removed"));
      closeDialog();
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  const handleVerify = async (values: { password: string }) => {
    setError(null);
    try {
      const session = await loginPassword(values);
      if (session.totpRequiredOnLogin && !session.passwordSessionActive) {
        setVerifyStep("totp");
        return;
      }
      await refreshSession();
      toast.success(t("settings.password.toasts.sessionEstablished"));
      closeDialog();
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  const handleVerifyTotp = async (values: { code: string }) => {
    setError(null);
    try {
      await verifyTotp(values);
      await refreshSession();
      toast.success(t("settings.password.toasts.sessionEstablished"));
      closeDialog();
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  const statusMessage = !passwordManagementEnabled
    ? t("settings.password.status.disabled")
    : authMode === "trusted_header"
      ? passwordRequired
        ? t("settings.password.status.fallbackConfigured")
        : t("settings.password.status.fallbackMissing")
      : passwordRequired
        ? t("settings.password.status.configured")
        : t("settings.password.status.notSet");

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <KeyRound className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">{t("settings.password.title")}</h3>
            <p className="text-xs text-muted-foreground">{statusMessage}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {!passwordManagementEnabled ? null : passwordRequired && passwordSessionActive ? (
            <>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs"
                disabled={lock}
                onClick={() => setActiveDialog("change")}
              >
                {t("settings.password.actions.change")}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs text-destructive hover:text-destructive"
                disabled={lock}
                onClick={() => setActiveDialog("remove")}
              >
                {t("settings.password.actions.remove")}
              </Button>
            </>
          ) : passwordRequired && authenticated && !passwordSessionActive ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8 text-xs"
              disabled={disabled}
              onClick={() => setActiveDialog("verify")}
            >
              {t("settings.password.actions.loginToManage")}
            </Button>
          ) : !passwordRequired ? (
            <Button
              type="button"
              size="sm"
              className="h-8 text-xs"
              disabled={lock}
              onClick={() => setActiveDialog("setup")}
            >
              {t("settings.password.actions.set")}
            </Button>
          ) : null}
        </div>
        </div>
      </div>

      {/* Setup dialog */}
      <Dialog open={activeDialog === "setup"} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{t("settings.password.setupDialog.title")}</DialogTitle>
              <DialogDescription>{t("settings.password.setupDialog.description")}</DialogDescription>
            </DialogHeader>
            {bootstrapRequired ? (
              <AlertMessage variant="error">
                {bootstrapTokenConfigured
                  ? t("settings.password.setupDialog.bootstrapTokenConfigured")
                  : t("settings.password.setupDialog.bootstrapTokenMissing")}
              </AlertMessage>
            ) : null}
            {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
            <Form {...setupForm}>
              <form onSubmit={setupForm.handleSubmit(handleSetup)} className="space-y-4">
              <FormField
                control={setupForm.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("settings.password.setupDialog.passwordLabel")}</FormLabel>
                    <FormControl>
                      <Input {...field} type="password" autoComplete="new-password" placeholder={t("settings.password.setupDialog.passwordPlaceholder")} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
                />
                {bootstrapRequired ? (
                  <FormField
                    control={setupForm.control}
                    name="bootstrapToken"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>{t("settings.password.setupDialog.bootstrapTokenLabel")}</FormLabel>
                        <FormControl>
                          <Input {...field} type="password" autoComplete="one-time-code" placeholder={t("settings.password.setupDialog.bootstrapTokenPlaceholder")} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                ) : null}
                <DialogFooter>
                <Button type="button" variant="outline" onClick={closeDialog} disabled={busy}>
                  {t("common.cancel")}
                </Button>
                <Button type="submit" disabled={lock}>
                  {t("settings.password.setupDialog.submit")}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>

      {/* Change dialog */}
      <Dialog open={activeDialog === "change"} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("settings.password.changeDialog.title")}</DialogTitle>
            <DialogDescription>{t("settings.password.changeDialog.description")}</DialogDescription>
          </DialogHeader>
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
          <Form {...changeForm}>
            <form onSubmit={changeForm.handleSubmit(handleChange)} className="space-y-4">
              <FormField
                control={changeForm.control}
                name="currentPassword"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("settings.password.changeDialog.currentLabel")}</FormLabel>
                    <FormControl>
                      <Input {...field} type="password" autoComplete="current-password" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={changeForm.control}
                name="newPassword"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("settings.password.changeDialog.newLabel")}</FormLabel>
                    <FormControl>
                      <Input {...field} type="password" autoComplete="new-password" placeholder={t("settings.password.changeDialog.newPlaceholder")} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={closeDialog} disabled={busy}>
                  {t("common.cancel")}
                </Button>
                <Button type="submit" disabled={lock}>
                  {t("settings.password.changeDialog.submit")}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>

      {/* Remove dialog */}
      <Dialog open={activeDialog === "remove"} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("settings.password.removeDialog.title")}</DialogTitle>
            <DialogDescription>{t("settings.password.removeDialog.description")}</DialogDescription>
          </DialogHeader>
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
          <Form {...removeForm}>
            <form onSubmit={removeForm.handleSubmit(handleRemove)} className="space-y-4">
              <FormField
                control={removeForm.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("settings.password.removeDialog.currentLabel")}</FormLabel>
                    <FormControl>
                      <Input {...field} type="password" autoComplete="current-password" placeholder={t("settings.password.removeDialog.currentPlaceholder")} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={closeDialog} disabled={busy}>
                  {t("common.cancel")}
                </Button>
                <Button type="submit" variant="destructive" disabled={lock}>
                  {t("settings.password.removeDialog.submit")}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>

      {/* Verify dialog (re-establish password session for proxy-authenticated users) */}
      <Dialog open={activeDialog === "verify"} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {verifyStep === "password"
                ? t("settings.password.verifyDialog.title")
                : t("settings.password.verifyDialog.totpTitle")}
            </DialogTitle>
            <DialogDescription>
              {verifyStep === "password"
                ? t("settings.password.verifyDialog.description")
                : t("settings.password.verifyDialog.totpDescription")}
            </DialogDescription>
          </DialogHeader>
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
          {verifyStep === "password" ? (
            <Form {...verifyForm}>
              <form onSubmit={verifyForm.handleSubmit(handleVerify)} className="space-y-4">
                <FormField
                  control={verifyForm.control}
                  name="password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("settings.password.verifyDialog.passwordLabel")}</FormLabel>
                      <FormControl>
                        <Input {...field} type="password" autoComplete="current-password" placeholder={t("settings.password.verifyDialog.passwordPlaceholder")} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <DialogFooter>
                  <Button type="button" variant="outline" onClick={closeDialog} disabled={busy}>
                    {t("common.cancel")}
                  </Button>
                  <Button type="submit" disabled={busy}>
                    {t("settings.password.verifyDialog.submit")}
                  </Button>
                </DialogFooter>
              </form>
            </Form>
          ) : (
            <Form {...verifyTotpForm}>
              <form onSubmit={verifyTotpForm.handleSubmit(handleVerifyTotp)} className="space-y-4">
                <FormField
                  control={verifyTotpForm.control}
                  name="code"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("settings.password.verifyDialog.totpLabel")}</FormLabel>
                      <FormControl>
                        <Input {...field} type="text" inputMode="numeric" autoComplete="one-time-code" placeholder={t("settings.password.verifyDialog.totpPlaceholder")} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <DialogFooter>
                  <Button type="button" variant="outline" onClick={closeDialog} disabled={busy}>
                    {t("common.cancel")}
                  </Button>
                  <Button type="submit" disabled={busy}>
                    {t("settings.password.verifyDialog.submit")}
                  </Button>
                </DialogFooter>
              </form>
            </Form>
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}
