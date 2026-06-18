import { zodResolver } from "@hookform/resolvers/zod";
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

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      form.reset();
      form.clearErrors();
    }
    onOpenChange(next);
  };

  const handleSubmit = async (values: { password: string; bootstrapToken?: string }) => {
    form.clearErrors("root");
    try {
      await setupPassword({
        password: values.password,
        bootstrapToken: values.bootstrapToken?.trim() ? values.bootstrapToken.trim() : undefined,
      });
      await refreshSession();
      toast.success("Password configured");
      handleOpenChange(false);
    } catch (caught) {
      form.setError("root", { message: getErrorMessage(caught) });
    }
  };

  const rootError = form.formState.errors.root?.message;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
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
        {rootError ? <AlertMessage variant="error">{rootError}</AlertMessage> : null}
        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-4">
            <FormField
              control={form.control}
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
                control={form.control}
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
              <Button type="button" variant="outline" onClick={() => handleOpenChange(false)} disabled={busy}>
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
  );
}
