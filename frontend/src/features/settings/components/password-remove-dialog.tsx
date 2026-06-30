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
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { removePassword } from "@/features/auth/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { PasswordRemoveRequestSchema } from "@/features/auth/schemas";
import { getErrorMessage } from "@/utils/errors";

export type PasswordRemoveDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  disabled?: boolean;
};

export function PasswordRemoveDialog({
  open,
  onOpenChange,
  disabled = false,
}: PasswordRemoveDialogProps) {
  const { t } = useTranslation();
  const passwordManagementEnabled = useAuthStore(
    (s) => s.passwordManagementEnabled,
  );
  const refreshSession = useAuthStore((s) => s.refreshSession);

  const resolveValidationMessage = (message?: string): string | undefined => {
    if (!message) {
      return undefined;
    }
    const translated = t(message);
    return translated === message ? message : translated;
  };

  const form = useForm({
    resolver: zodResolver(PasswordRemoveRequestSchema),
    defaultValues: { password: "" },
  });

  const busy = form.formState.isSubmitting;
  const lock = busy || disabled || !passwordManagementEnabled;

  useEffect(() => {
    if (!open) {
      form.reset();
      form.clearErrors();
    }
  }, [open, form]);

  const handleSubmit = async (values: { password: string }) => {
    form.clearErrors("root");
    try {
      await removePassword(values);
      await refreshSession();
      toast.success(t("settings.password.toasts.removed"));
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
          <DialogTitle>{t("settings.password.removeDialog.title")}</DialogTitle>
          <DialogDescription>
            {t("settings.password.removeDialog.description")}
          </DialogDescription>
        </DialogHeader>
        {rootError ? (
          <AlertMessage variant="error">{rootError}</AlertMessage>
        ) : null}
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit)}
            className="space-y-4"
          >
            <FormField
              control={form.control}
              name="password"
              render={({ field, fieldState }) => (
                <FormItem>
                  <FormLabel>
                    {t("settings.password.removeDialog.currentLabel")}
                  </FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      type="password"
                      autoComplete="current-password"
                      placeholder={t(
                        "settings.password.removeDialog.currentPlaceholder",
                      )}
                    />
                  </FormControl>
                  {fieldState.error?.message ? (
                    <FormMessage>
                      {resolveValidationMessage(fieldState.error.message)}
                    </FormMessage>
                  ) : (
                    <FormMessage />
                  )}
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={busy}
              >
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
  );
}
