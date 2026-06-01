import { zodResolver } from "@hookform/resolvers/zod";
import { useCallback, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
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
  const refreshSession = useAuthStore((s) => s.refreshSession);

  const [step, setStep] = useState<VerifyStep>("password");
  const [error, setError] = useState<string | null>(null);

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
      toast.success("Password session established");
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
      toast.success("Password session established");
      handleOpenChange(false);
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{step === "password" ? "Verify password" : "TOTP verification"}</DialogTitle>
          <DialogDescription>
            {step === "password"
              ? "Enter your password to unlock password and TOTP management."
              : "Enter your TOTP code to complete verification."}
          </DialogDescription>
        </DialogHeader>
        {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
        {step === "password" ? (
          <Form {...passwordForm}>
            <form onSubmit={passwordForm.handleSubmit(handlePassword)} className="space-y-4">
              <FormField
                control={passwordForm.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Password</FormLabel>
                    <FormControl>
                      <Input {...field} type="password" autoComplete="current-password" placeholder="Enter current password" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => handleOpenChange(false)} disabled={busy}>
                  Cancel
                </Button>
                <Button type="submit" disabled={busy || disabled}>
                  Verify
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
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>TOTP code</FormLabel>
                    <FormControl>
                      <Input {...field} type="text" inputMode="numeric" autoComplete="one-time-code" placeholder="6-digit code" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => handleOpenChange(false)} disabled={busy}>
                  Cancel
                </Button>
                <Button type="submit" disabled={busy || disabled}>
                  Verify
                </Button>
              </DialogFooter>
            </form>
          </Form>
        )}
      </DialogContent>
    </Dialog>
  );
}
