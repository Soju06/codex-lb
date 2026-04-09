import { CodexLogo } from "@/components/brand/codex-logo";
import { AlertMessage } from "@/components/alert-message";
import { PasswordSettings } from "@/features/settings/components/password-settings";
import { useAuthStore } from "@/features/auth/hooks/use-auth";

export function BootstrapSetupScreen() {
  const bootstrapTokenConfigured = useAuthStore((state) => state.bootstrapTokenConfigured);

  return (
    <div className="relative flex min-h-screen items-center justify-center p-4">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-1/4 -right-1/4 h-[600px] w-[600px] rounded-full bg-primary/5 blur-3xl" />
        <div className="absolute -bottom-1/4 -left-1/4 h-[500px] w-[500px] rounded-full bg-primary/3 blur-3xl" />
        <div className="absolute bottom-0 left-1/2 h-[400px] w-[400px] -translate-x-1/2 rounded-full bg-primary/4 blur-3xl" />
      </div>

      <div className="relative w-full max-w-2xl space-y-6 animate-fade-in-up">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 shadow-sm ring-2 ring-primary/10 ring-offset-2 ring-offset-background">
            <CodexLogo size={28} className="text-primary" />
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Complete Remote Setup</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Remote dashboard access stays locked until an admin password is configured.
            </p>
          </div>
        </div>

        <div className="rounded-2xl border bg-card p-6 shadow-[var(--shadow-md)]">
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Use the password setup flow below to bootstrap dashboard access before loading the rest of the admin UI.
            </p>
            <AlertMessage variant="error">
              {bootstrapTokenConfigured
                ? "Find the bootstrap token in your server logs (docker logs <container>) and enter it below with your new password."
                : "Remote setup is blocked. Set CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN on the server or restart without a password to auto-generate one."}
            </AlertMessage>
          </div>
        </div>

        <PasswordSettings />
      </div>
    </div>
  );
}
