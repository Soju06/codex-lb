import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { DialogFooter } from "@/components/ui/dialog";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { InputOTP, InputOTPGroup, InputOTPSeparator, InputOTPSlot } from "@/components/ui/input-otp";
import { confirmTotpSetup, startTotpSetup } from "@/features/auth/api";
import { TotpVerifyRequestSchema } from "@/features/auth/schemas";
import { getErrorMessage } from "@/utils/errors";

export type TotpEnrollmentFormProps = {
  /** Called with the confirmed code after the backend accepted the secret. */
  onEnrolled: (code: string) => Promise<void> | void;
  onCancel?: () => void;
  cancelLabel?: string;
  disabled?: boolean;
};

/**
 * Starts a TOTP enrollment on mount (QR code + secret), then confirms the first
 * code. Shared by the Settings TOTP card, the login-time enrollment gate and the
 * invite acceptance screen so every place enrols through the same two calls.
 */
export function TotpEnrollmentForm({ onEnrolled, onCancel, cancelLabel, disabled = false }: TotpEnrollmentFormProps) {
  const { t } = useTranslation();
  const [secret, setSecret] = useState<string | null>(null);
  const [qrDataUri, setQrDataUri] = useState<string | null>(null);
  const [starting, setStarting] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const form = useForm({
    resolver: zodResolver(TotpVerifyRequestSchema),
    defaultValues: { code: "" },
  });

  useEffect(() => {
    let cancelled = false;
    startTotpSetup()
      .then((response) => {
        if (cancelled) return;
        setSecret(response.secret);
        setQrDataUri(response.qrSvgDataUri);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(getErrorMessage(caught));
      })
      .finally(() => {
        if (!cancelled) setStarting(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const lock = disabled || starting || form.formState.isSubmitting;

  const handleConfirm = async (values: { code: string }) => {
    if (!secret) return;
    setError(null);
    try {
      const code = values.code.trim();
      await confirmTotpSetup({ secret, code });
      await onEnrolled(code);
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  return (
    <div className="space-y-4">
      {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}

      {qrDataUri ? (
        <div className="flex justify-center rounded-lg border bg-card p-4 dark:bg-white/95">
          <img src={qrDataUri} alt={t("settings.totp.setupDialog.qrAlt")} className="h-40 w-40" />
        </div>
      ) : null}

      {secret ? (
        <p className="rounded-lg border bg-muted/30 px-3 py-2 font-mono text-xs">
          {t("settings.totp.setupDialog.secretLabel")} {secret}
        </p>
      ) : null}

      {secret ? (
        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleConfirm)} className="space-y-4">
            <FormField
              control={form.control}
              name="code"
              render={({ field }) => (
                <FormItem className="flex flex-col items-center gap-2">
                  <FormLabel className="sr-only">{t("settings.totp.setupDialog.codeLabel")}</FormLabel>
                  <FormControl>
                    <InputOTP maxLength={6} value={field.value} onChange={field.onChange}>
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
            <DialogFooter>
              {onCancel ? (
                <Button type="button" variant="outline" onClick={onCancel} disabled={starting}>
                  {cancelLabel ?? t("common.cancel")}
                </Button>
              ) : null}
              <Button type="submit" disabled={lock}>
                {t("settings.totp.setupDialog.submit")}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      ) : null}
    </div>
  );
}
