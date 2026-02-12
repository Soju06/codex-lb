import { Navigate, Outlet, Route, Routes } from "react-router-dom";

import { AppHeader } from "@/components/layout/app-header";
import { StatusBar } from "@/components/layout/status-bar";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AccountsPage } from "@/features/accounts/components/accounts-page";
import { AuthGate } from "@/features/auth/components/auth-gate";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { DashboardPage } from "@/features/dashboard/components/dashboard-page";
import { SettingsPage } from "@/features/settings/components/settings-page";
import { useThemeStore } from "@/hooks/use-theme";

function AppLayout() {
  const theme = useThemeStore((state) => state.theme);
  const toggleTheme = useThemeStore((state) => state.toggleTheme);
  const logout = useAuthStore((state) => state.logout);

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <AppHeader
        theme={theme}
        onThemeToggle={toggleTheme}
        onLogout={() => {
          void logout();
        }}
      />
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">
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
          </Route>
        </Routes>
      </AuthGate>
    </TooltipProvider>
  );
}
