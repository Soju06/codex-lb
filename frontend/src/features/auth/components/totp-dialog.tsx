import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { InputOTP, InputOTPGroup, InputOTPSeparator, InputOTPSlot } from "@/components/ui/input-otp";
import { Spinner } from "@/components/ui/spinner";
import { TotpVerifyRequestSchema } from "@/features/auth/schemas";
import { useAuthStore } from "@/features/auth/hooks/use-auth";

export type TotpDialogProps = {
  open: boolean;
};

export function TotpDialog({ open }: TotpDialogProps) {
  const { t } = useTranslation();
  const loading = useAuthStore((state) => state.loading);
  const error = useAuthStore((state) => state.error);
  const clearError = useAuthStore((state) => state.clearError);
  const verifyTotp = useAuthStore((state) => state.verifyTotp);
  const logout = useAuthStore((state) => state.logout);

  const form = useForm({
    resolver: zodResolver(TotpVerifyRequestSchema),
    defaultValues: { code: "" },
  });

  const handleSubmit = async (values: { code: string }) => {
    clearError();
    await verifyTotp(values.code.trim());
    form.reset();
  };

  const handleCancel = () => {
    form.reset();
    clearError();
    void logout();
  };

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && handleCancel()}>
      <DialogContent
        className="sm:max-w-sm"
        onInteractOutside={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>{t("auth.totp.title")}</DialogTitle>
          <DialogDescription>{t("auth.totp.description")}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-5">
            <FormField
              control={form.control}
              name="code"
              render={({ field }) => (
                <FormItem className="flex flex-col items-center gap-2">
                  <FormLabel className="sr-only">{t("auth.totp.codeLabel")}</FormLabel>
                  <FormControl>
                    <InputOTP
                      maxLength={6}
                      autoFocus
                      value={field.value}
                      onChange={field.onChange}
                      onComplete={() => form.handleSubmit(handleSubmit)()}
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
                  <FormMessage />
                </FormItem>
              )}
            />

            {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? <Spinner size="sm" className="mr-2" /> : null}
              {t("auth.totp.submit")}
            </Button>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
