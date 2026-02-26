import { Navigate, Outlet, Route, Routes } from "react-router-dom";

import { AppHeader } from "@/components/layout/app-header";
import { StatusBar } from "@/components/layout/status-bar";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthGate } from "@/features/auth/components/auth-gate";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { AccountsPage } from "@/features/accounts/components/accounts-page";
import { DashboardPage } from "@/features/dashboard/components/dashboard-page";
import { FirewallPage } from "@/features/firewall/components/firewall-page";
import { SettingsPage } from "@/features/settings/components/settings-page";

function AppLayout() {
  const logout = useAuthStore((state) => state.logout);
  const passwordRequired = useAuthStore((state) => state.passwordRequired);

  return (
    <div className="flex min-h-screen flex-col bg-background pb-10">
      <AppHeader
        onLogout={() => {
          void logout();
        }}
        showLogout={passwordRequired}
      />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">
        <Outlet />
      </main>
      <StatusBar />
    </div>
  );
}

export default function App() {
  return (
    <TooltipProvider>
      <Toaster richColors />
      <AuthGate>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/accounts" element={<AccountsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/firewall" element={<FirewallPage />} />
          </Route>
        </Routes>
      </AuthGate>
    </TooltipProvider>
  );
}
