import { Navigate, Outlet, Route, Routes } from "react-router-dom";

import { AppHeader } from "@/components/layout/app-header";
import { StatusBar } from "@/components/layout/status-bar";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthGate } from "@/features/auth/components/auth-gate";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { AccountsPage } from "@/features/accounts/components/accounts-page";
import { ClaudeAccountsPage } from "@/features/claude/components/claude-accounts-page";
import { ApisPage } from "@/features/apis/components/apis-page";
import { DashboardPage } from "@/features/dashboard/components/dashboard-page";
import { ReportsPage } from "@/features/reports/components/reports-page";
import { SettingsPage } from "@/features/settings/components/settings-page";
import { useTimeFormatStore } from "@/hooks/use-time-format";

function AppLayout() {
  const logout = useAuthStore((state) => state.logout);
  const passwordRequired = useAuthStore((state) => state.passwordRequired);
  const role = useAuthStore((state) => state.role);
  const guestPasswordRequired = useAuthStore((state) => state.guestPasswordRequired);
  const startAdminLogin = useAuthStore((state) => state.startAdminLogin);
  const timeFormat = useTimeFormatStore((state) => state.timeFormat);
  const isGuest = role === "guest";

  return (
    <div className="flex min-h-screen flex-col bg-background pb-10" data-time-format={timeFormat}>
      <AppHeader
        onLogout={() => {
          void logout();
        }}
        onAdminLogin={startAdminLogin}
        showAdminLogin={isGuest && passwordRequired}
        showLogout={(role === "admin" && passwordRequired) || (isGuest && guestPasswordRequired)}
      />
      <main className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-8 sm:px-6">
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
            <Route path="/reports" element={<ReportsPage />} />
            <Route path="/accounts" element={<AccountsPage />} />
            <Route path="/claude-accounts" element={<ClaudeAccountsPage />} />
            <Route path="/apis" element={<ApisPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/firewall" element={<Navigate to="/settings" replace />} />
          </Route>
        </Routes>
      </AuthGate>
    </TooltipProvider>
  );
}
