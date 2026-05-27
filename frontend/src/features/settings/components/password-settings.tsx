import { KeyRound } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { PasswordChangeDialog } from "@/features/settings/components/password-change-dialog";
import { PasswordRemoveDialog } from "@/features/settings/components/password-remove-dialog";
import { PasswordSetupDialog } from "@/features/settings/components/password-setup-dialog";
import { PasswordVerifyDialog } from "@/features/settings/components/password-verify-dialog";

type PasswordDialog = "setup" | "change" | "remove" | "verify" | null;

export type PasswordSettingsProps = {
  disabled?: boolean;
};

export function PasswordSettings({ disabled = false }: PasswordSettingsProps) {
  const passwordRequired = useAuthStore((s) => s.passwordRequired);
  const authMode = useAuthStore((s) => s.authMode);
  const passwordManagementEnabled = useAuthStore((s) => s.passwordManagementEnabled);
  const passwordSessionActive = useAuthStore((s) => s.passwordSessionActive);
  const authenticated = useAuthStore((s) => s.authenticated);

  const [activeDialog, setActiveDialog] = useState<PasswordDialog>(null);

  const lock = disabled || !passwordManagementEnabled;
  const closeIfMatches = (dialog: PasswordDialog) => (open: boolean) => {
    if (!open && activeDialog === dialog) {
      setActiveDialog(null);
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

      <PasswordSetupDialog
        open={activeDialog === "setup"}
        onOpenChange={closeIfMatches("setup")}
        disabled={disabled}
      />
      <PasswordChangeDialog
        open={activeDialog === "change"}
        onOpenChange={closeIfMatches("change")}
        disabled={disabled}
      />
      <PasswordRemoveDialog
        open={activeDialog === "remove"}
        onOpenChange={closeIfMatches("remove")}
        disabled={disabled}
      />
      <PasswordVerifyDialog
        open={activeDialog === "verify"}
        onOpenChange={closeIfMatches("verify")}
        disabled={disabled}
      />
    </section>
  );
}
