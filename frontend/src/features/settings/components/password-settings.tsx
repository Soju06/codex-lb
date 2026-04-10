import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRound } from "lucide-react";
import { useState } from "react";
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
      toast.success("Password configured");
      closeDialog();
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  const handleChange = async (values: { currentPassword: string; newPassword: string }) => {
    setError(null);
    try {
      await changePassword(values);
      toast.success("Password changed");
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
      toast.success("Password removed");
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
      toast.success("Password session established");
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
      toast.success("Password session established");
      closeDialog();
    } catch (caught) {
      setError(getErrorMessage(caught));
    }
  };

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
            <KeyRound className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">Password</h3>
            <p className="text-xs text-muted-foreground">
              {!passwordManagementEnabled
                ? "Password login is disabled by the current dashboard auth mode."
                : authMode === "trusted_header"
                  ? passwordRequired
                    ? "Password is configured as an optional fallback."
                    : "No fallback password set."
                  : passwordRequired
                    ? "Password is configured."
                    : "No password set."}
            </p>
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
                Change
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 text-xs text-destructive hover:text-destructive"
                disabled={lock}
                onClick={() => setActiveDialog("remove")}
              >
                Remove
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
              Login to manage
            </Button>
          ) : !passwordRequired ? (
            <Button
              type="button"
              size="sm"
              className="h-8 text-xs"
              disabled={lock}
              onClick={() => setActiveDialog("setup")}
            >
              Set password
            </Button>
          ) : null}
        </div>
        </div>
      </div>

      {/* Setup dialog */}
      <Dialog open={activeDialog === "setup"} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Set password</DialogTitle>
              <DialogDescription>Set a password for dashboard login.</DialogDescription>
            </DialogHeader>
            {bootstrapRequired ? (
              <AlertMessage variant="error">
                {bootstrapTokenConfigured
                  ? "Remote setup requires the configured bootstrap token (from server logs or CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN)."
                  : "Remote setup is blocked. Set CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN on the server or restart to auto-generate a token."}
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
                    <FormLabel>Password</FormLabel>
                    <FormControl>
                      <Input {...field} type="password" autoComplete="new-password" placeholder="Min. 8 characters" />
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
                        <FormLabel>Bootstrap token</FormLabel>
                        <FormControl>
                          <Input {...field} type="password" autoComplete="one-time-code" placeholder="Enter bootstrap token" />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                ) : null}
                <DialogFooter>
                <Button type="button" variant="outline" onClick={closeDialog} disabled={busy}>
                  Cancel
                </Button>
                <Button type="submit" disabled={lock}>
                  Set password
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
            <DialogTitle>Change password</DialogTitle>
            <DialogDescription>Enter your current password and a new one.</DialogDescription>
          </DialogHeader>
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
          <Form {...changeForm}>
            <form onSubmit={changeForm.handleSubmit(handleChange)} className="space-y-4">
              <FormField
                control={changeForm.control}
                name="currentPassword"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Current password</FormLabel>
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
                    <FormLabel>New password</FormLabel>
                    <FormControl>
                      <Input {...field} type="password" autoComplete="new-password" placeholder="Min. 8 characters" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={closeDialog} disabled={busy}>
                  Cancel
                </Button>
                <Button type="submit" disabled={lock}>
                  Change password
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
            <DialogTitle>Remove password</DialogTitle>
            <DialogDescription>Confirm your current password to remove it.</DialogDescription>
          </DialogHeader>
          {error ? <AlertMessage variant="error">{error}</AlertMessage> : null}
          <Form {...removeForm}>
            <form onSubmit={removeForm.handleSubmit(handleRemove)} className="space-y-4">
              <FormField
                control={removeForm.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Current password</FormLabel>
                    <FormControl>
                      <Input {...field} type="password" autoComplete="current-password" placeholder="Enter current password" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={closeDialog} disabled={busy}>
                  Cancel
                </Button>
                <Button type="submit" variant="destructive" disabled={lock}>
                  Remove password
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
            <DialogTitle>{verifyStep === "password" ? "Verify password" : "TOTP verification"}</DialogTitle>
            <DialogDescription>
              {verifyStep === "password"
                ? "Enter your password to unlock password and TOTP management."
                : "Enter your TOTP code to complete verification."}
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
                      <FormLabel>Password</FormLabel>
                      <FormControl>
                        <Input {...field} type="password" autoComplete="current-password" placeholder="Enter current password" />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <DialogFooter>
                  <Button type="button" variant="outline" onClick={closeDialog} disabled={busy}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={busy}>
                    Verify
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
                      <FormLabel>TOTP code</FormLabel>
                      <FormControl>
                        <Input {...field} type="text" inputMode="numeric" autoComplete="one-time-code" placeholder="6-digit code" />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <DialogFooter>
                  <Button type="button" variant="outline" onClick={closeDialog} disabled={busy}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={busy}>
                    Verify
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
