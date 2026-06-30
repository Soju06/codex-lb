import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect } from "react";
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
import { setupPassword } from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { PasswordSetupRequestSchema } from "@/features/auth/schemas";
import { getErrorMessage } from "@/utils/errors";

export type PasswordSetupDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  disabled?: boolean;
};

export function PasswordSetupDialog({ open, onOpenChange, disabled = false }: PasswordSetupDialogProps) {
  const { t } = useTranslation();
  const bootstrapRequired = useAuthStore((s) => s.bootstrapRequired);
  const bootstrapTokenConfigured = useAuthStore((s) => s.bootstrapTokenConfigured);
  const passwordManagementEnabled = useAuthStore((s) => s.passwordManagementEnabled);
  const refreshSession = useAuthStore((s) => s.refreshSession);

  const form = useForm({
    resolver: zodResolver(PasswordSetupRequestSchema),
    defaultValues: { password: "", bootstrapToken: "" },
  });

  const busy = form.formState.isSubmitting;
  const lock = busy || disabled || !passwordManagementEnabled;
  const resolveValidationMessage = (message?: string): string | undefined => {
    if (!message) {
      return undefined;
    }
    const translated = t(message);
    return translated === message ? message : translated;
  };

  useEffect(() => {
    if (!open) {
      form.reset();
      form.clearErrors();
    }
  }, [open, form]);

  const handleSubmit = async (values: { password: string; bootstrapToken?: string }) => {
    form.clearErrors("root");
    try {
      await setupPassword({
        password: values.password,
        bootstrapToken: values.bootstrapToken?.trim() ? values.bootstrapToken.trim() : undefined,
      });
      await refreshSession();
      toast.success(t("settings.password.toasts.configured"));
      onOpenChange(false);
    } catch (caught) {
      form.setError("root", { message: getErrorMessage(caught) });
    }
  };

  const rootError = form.formState.errors.root?.message;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
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
        {rootError ? <AlertMessage variant="error">{rootError}</AlertMessage> : null}
        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="password"
              render={({ field, fieldState }) => (
                <FormItem>
                  <FormLabel>{t("settings.password.setupDialog.passwordLabel")}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      type="password"
                      autoComplete="new-password"
                      placeholder={t("settings.password.setupDialog.passwordPlaceholder")}
                    />
                  </FormControl>
                  {fieldState.error?.message ? <FormMessage>{resolveValidationMessage(fieldState.error.message)}</FormMessage> : <FormMessage />}
                </FormItem>
              )}
            />
            {bootstrapRequired ? (
              <FormField
                control={form.control}
                name="bootstrapToken"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("settings.password.setupDialog.bootstrapTokenLabel")}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        type="password"
                        autoComplete="one-time-code"
                        placeholder={t("settings.password.setupDialog.bootstrapTokenPlaceholder")}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ) : null}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
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
  );
}
