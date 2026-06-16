import { zodResolver } from "@hookform/resolvers/zod";
import { useCallback, useEffect, useState } from "react";
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
import { loginPassword, verifyTotp } from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { PasswordRemoveRequestSchema, TotpVerifyRequestSchema } from "@/features/auth/schemas";
import { getErrorMessage } from "@/utils/errors";

type VerifyStep = "password" | "totp";

export type PasswordVerifyDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  disabled?: boolean;
};

export function PasswordVerifyDialog({ open, onOpenChange, disabled = false }: PasswordVerifyDialogProps) {
  const { t } = useTranslation();
  const refreshSession = useAuthStore((s) => s.refreshSession);

  const [step, setStep] = useState<VerifyStep>("password");
  const [error, setError] = useState<string | null>(null);
  const resolveValidationMessage = (message?: string): string | undefined => {
    if (!message) {
      return undefined;
    }
    const translated = t(message);
    return translated === message ? message : translated;
  };

  const passwordForm = useForm({
    resolver: zodResolver(PasswordRemoveRequestSchema),
    defaultValues: { password: "" },
  });

  const totpForm = useForm({
    resolver: zodResolver(TotpVerifyRequestSchema),
    defaultValues: { code: "" },
  });

  const busy = passwordForm.formState.isSubmitting || totpForm.formState.isSubmitting;

  const resetAll = useCallback(() => {
    passwordForm.reset();
    totpForm.reset();
    setStep("password");
    setError(null);
  }, [passwordForm, totpForm]);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) {
        resetAll();
      }
      onOpenChange(next);
    },
    [onOpenChange, resetAll],
  );

  useEffect(() => {
    if (!open) {
      passwordForm.reset();
      totpForm.reset();
    }
  }, [open, passwordForm, totpForm]);

  const handlePassword = async (values: { password: string }) => {
    setError(null);
    try {
      const session = await loginPassword(values);
      if (session.totpRequiredOnLogin && !session.passwordSessionActive) {
        setStep("totp");
        return;
      }
      await refreshSession();
      toast.success(t("settings.password.toasts.sessionEstablished"));
      handleOpenChange(false);
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  const handleTotp = async (values: { code: string }) => {
    setError(null);
    try {
      await verifyTotp(values);
      await refreshSession();
      toast.success(t("settings.password.toasts.sessionEstablished"));
      handleOpenChange(false);
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {step === "password" ? t("settings.password.verifyDialog.title") : t("settings.password.verifyDialog.totpTitle")}
          </DialogTitle>
          <DialogDescription>
            {step === "password"
              ? t("settings.password.verifyDialog.description")
              : t("settings.password.verifyDialog.totpDescription")}
          </DialogDescription>
        </DialogHeader>
        {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
        {step === "password" ? (
          <Form {...passwordForm}>
            <form onSubmit={passwordForm.handleSubmit(handlePassword)} className="space-y-4">
              <FormField
                control={passwordForm.control}
                name="password"
                render={({ field, fieldState }) => (
                  <FormItem>
                    <FormLabel>{t("settings.password.verifyDialog.passwordLabel")}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        type="password"
                        autoComplete="current-password"
                        placeholder={t("settings.password.verifyDialog.passwordPlaceholder")}
                      />
                    </FormControl>
                    {fieldState.error?.message ? (
                      <FormMessage>{resolveValidationMessage(fieldState.error.message)}</FormMessage>
                    ) : (
                      <FormMessage />
                    )}
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => handleOpenChange(false)} disabled={busy}>
                  {t("common.cancel")}
                </Button>
                <Button type="submit" disabled={busy || disabled}>
                  {t("settings.password.verifyDialog.submit")}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        ) : (
          <Form {...totpForm}>
            <form onSubmit={totpForm.handleSubmit(handleTotp)} className="space-y-4">
              <FormField
                control={totpForm.control}
                name="code"
                render={({ field, fieldState }) => (
                  <FormItem>
                    <FormLabel>{t("settings.password.verifyDialog.totpLabel")}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        type="text"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        placeholder={t("settings.password.verifyDialog.totpPlaceholder")}
                      />
                    </FormControl>
                    {fieldState.error?.message ? (
                      <FormMessage>{resolveValidationMessage(fieldState.error.message)}</FormMessage>
                    ) : (
                      <FormMessage />
                    )}
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => handleOpenChange(false)} disabled={busy}>
                  {t("common.cancel")}
                </Button>
                <Button type="submit" disabled={busy || disabled}>
                  {t("settings.password.verifyDialog.submit")}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        )}
      </DialogContent>
    </Dialog>
  );
}
