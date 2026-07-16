import { lazy, Suspense } from "react";
import { Navigate, Outlet, Route, Routes } from "react-router-dom";

import { AppHeader } from "@/components/layout/app-header";
import { StatusBar } from "@/components/layout/status-bar";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthGate } from "@/features/auth/components/auth-gate";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { useTimeFormatStore } from "@/hooks/use-time-format";

// Route-level code splitting: only the visited page's chunk loads, instead
// of one entry bundle carrying all six pages' code.
const DashboardPage = lazy(() =>
  import("@/features/dashboard/components/dashboard-page").then((m) => ({ default: m.DashboardPage })),
);
const ReportsPage = lazy(() =>
  import("@/features/reports/components/reports-page").then((m) => ({ default: m.ReportsPage })),
);
const AccountsPage = lazy(() =>
  import("@/features/accounts/components/accounts-page").then((m) => ({ default: m.AccountsPage })),
);
const AutomationsPage = lazy(() =>
  import("@/features/automations/components/automations-page").then((m) => ({ default: m.AutomationsPage })),
);
const ApisPage = lazy(() => import("@/features/apis/components/apis-page").then((m) => ({ default: m.ApisPage })));
const SettingsPage = lazy(() =>
  import("@/features/settings/components/settings-page").then((m) => ({ default: m.SettingsPage })),
);

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
        <Suspense fallback={null}>
          <Outlet />
        </Suspense>
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
            <Route path="/automations" element={<AutomationsPage />} />
            <Route path="/apis" element={<ApisPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/firewall" element={<Navigate to="/settings" replace />} />
          </Route>
        </Routes>
      </AuthGate>
    </TooltipProvider>
  );
}
