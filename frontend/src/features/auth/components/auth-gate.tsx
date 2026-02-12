import { Loader2 } from "lucide-react";
import { useEffect } from "react";
import type { PropsWithChildren } from "react";

import { LoginForm } from "@/features/auth/components/login-form";
import { TotpDialog } from "@/features/auth/components/totp-dialog";
import { useAuthStore } from "@/features/auth/hooks/use-auth";

export function AuthGate({ children }: PropsWithChildren) {
  const refreshSession = useAuthStore((state) => state.refreshSession);
  const initialized = useAuthStore((state) => state.initialized);
  const loading = useAuthStore((state) => state.loading);
  const passwordRequired = useAuthStore((state) => state.passwordRequired);
  const authenticated = useAuthStore((state) => state.authenticated);
  const totpRequiredOnLogin = useAuthStore((state) => state.totpRequiredOnLogin);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  if (!initialized && loading) {
    return (
      <div className="flex min-h-screen items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Checking session...
      </div>
    );
  }

  if (passwordRequired && !authenticated) {
    if (totpRequiredOnLogin) {
      return <TotpDialog open />;
    }
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="w-full max-w-sm">
          <LoginForm />
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
