import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Shield } from "lucide-react";
import { useReducer } from "react";
import { useForm } from "react-hook-form";
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

const totpCodeSchema = z.object({
  code: z.string().length(6, "Enter a 6-digit code"),
});

type TotpCodeValues = z.infer<typeof totpCodeSchema>;
type TotpDialog = "setup" | "disable" | null;

type TotpSettingsState = {
  activeDialog: TotpDialog;
  setupSecret: string | null;
  setupQrDataUri: string | null;
  prefetching: boolean;
  error: string | null;
};

const initialTotpSettingsState: TotpSettingsState = {
  activeDialog: null,
  setupSecret: null,
  setupQrDataUri: null,
  prefetching: false,
  error: null,
};

function totpSettingsReducer(
  state: TotpSettingsState,
  patch: Partial<TotpSettingsState>,
): TotpSettingsState {
  return { ...state, ...patch };
}

export type TotpSettingsProps = {
  settings: DashboardSettings;
  disabled?: boolean;
  onSave: (payload: SettingsUpdateRequest) => Promise<void>;
};

export function TotpSettings({ settings, disabled = false, onSave }: TotpSettingsProps) {
  const queryClient = useQueryClient();
  const refreshSession = useAuthStore((state) => state.refreshSession);

  const [state, updateState] = useReducer(totpSettingsReducer, initialTotpSettingsState);

  const confirmForm = useForm<TotpCodeValues>({
    resolver: zodResolver(totpCodeSchema),
    defaultValues: { code: "" },
  });

  const disableForm = useForm<TotpCodeValues>({
    resolver: zodResolver(totpCodeSchema),
    defaultValues: { code: "" },
  });

  const lock = disabled || state.prefetching || confirmForm.formState.isSubmitting || disableForm.formState.isSubmitting;

  const closeDialog = () => {
    updateState(initialTotpSettingsState);
    confirmForm.reset();
    disableForm.reset();
  };

  const handleOpenSetup = async () => {
    updateState({ activeDialog: "setup", prefetching: true, error: null });
    try {
      const response = await startTotpSetup();
      updateState({ setupSecret: response.secret, setupQrDataUri: response.qrSvgDataUri });
    } catch (caught) {
      updateState({ error: getErrorMessage(caught) });
    } finally {
      updateState({ prefetching: false });
    }
  };

  const handleConfirmSetup = async (values: TotpCodeValues) => {
    if (!state.setupSecret) return;
    updateState({ error: null });
    try {
      await confirmTotpSetup({ secret: state.setupSecret, code: values.code });
      await refreshSession();
      void queryClient.invalidateQueries({ queryKey: ["settings", "detail"] });
      toast.success("TOTP configured");
      closeDialog();
    } catch (caught) {
      updateState({ error: getErrorMessage(caught) });
    }
  };

  const handleDisable = async (values: TotpCodeValues) => {
    updateState({ error: null });
    try {
      await disableTotp({ code: values.code });
      await refreshSession();
      void queryClient.invalidateQueries({ queryKey: ["settings", "detail"] });
      toast.success("TOTP disabled");
      closeDialog();
    } catch (caught) {
      updateState({ error: getErrorMessage(caught) });
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
              <h3 className="text-sm font-semibold">TOTP</h3>
              <p className="text-xs text-muted-foreground">
                {settings.totpConfigured ? "TOTP is configured." : "No TOTP configured."}
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
                onClick={() => updateState({ activeDialog: "disable" })}
              >
                Disable
              </Button>
            ) : (
              <Button
                type="button"
                size="sm"
                className="h-8 text-xs"
                disabled={lock}
                onClick={handleOpenSetup}
              >
                Enable TOTP
              </Button>
            )}
          </div>
        </div>

        {/* Require on login toggle */}
        <div className="flex items-center justify-between rounded-lg border p-3">
          <div>
            <p className="text-sm font-medium">Require TOTP on login</p>
            <p className="text-xs text-muted-foreground">Prompt for TOTP code after password login.</p>
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
      <Dialog open={state.activeDialog === "setup"} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Enable TOTP</DialogTitle>
            <DialogDescription>
              Scan the QR code with your authenticator app, then enter the verification code.
            </DialogDescription>
          </DialogHeader>
          {state.error ? <AlertMessage variant="error">{state.error}</AlertMessage> : null}

          {state.setupQrDataUri ? (
            <div className="flex justify-center rounded-lg border bg-card p-4 dark:bg-white/95">
              <img src={state.setupQrDataUri} alt="TOTP QR code" className="h-40 w-40" />
            </div>
          ) : null}

          {state.setupSecret ? (
            <p className="rounded-lg border bg-muted/30 px-3 py-2 font-mono text-xs">
              Secret: {state.setupSecret}
            </p>
          ) : null}

          {state.setupSecret ? (
            <Form {...confirmForm}>
              <form onSubmit={confirmForm.handleSubmit(handleConfirmSetup)} className="space-y-4">
                <FormField
                  control={confirmForm.control}
                  name="code"
                  render={({ field }) => (
                    <FormItem className="flex flex-col items-center gap-2">
                      <FormLabel className="sr-only">Verification code</FormLabel>
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
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <DialogFooter>
                  <Button type="button" variant="outline" onClick={closeDialog} disabled={state.prefetching}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={lock}>
                    Confirm setup
                  </Button>
                </DialogFooter>
              </form>
            </Form>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* Disable dialog */}
      <Dialog open={state.activeDialog === "disable"} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Disable TOTP</DialogTitle>
            <DialogDescription>Enter your current TOTP code to disable two-factor authentication.</DialogDescription>
          </DialogHeader>
          {state.error ? <AlertMessage variant="error">{state.error}</AlertMessage> : null}
          <Form {...disableForm}>
            <form onSubmit={disableForm.handleSubmit(handleDisable)} className="space-y-4">
              <FormField
                control={disableForm.control}
                name="code"
                render={({ field }) => (
                  <FormItem className="flex flex-col items-center gap-2">
                    <FormLabel className="sr-only">TOTP code</FormLabel>
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
                    <FormMessage />
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={closeDialog} disabled={lock}>
                  Cancel
                </Button>
                <Button type="submit" variant="destructive" disabled={lock}>
                  Disable TOTP
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>
    </section>
  );
}
