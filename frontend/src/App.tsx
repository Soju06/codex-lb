import { lazy, Suspense } from "react";
import { Navigate, Outlet, Route, Routes } from "react-router-dom";

import { AppHeader } from "@/components/layout/app-header";
import { StatusBar } from "@/components/layout/status-bar";
import { Toaster } from "@/components/ui/sonner";
import { SpinnerBlock } from "@/components/ui/spinner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthGate } from "@/features/auth/components/auth-gate";
import { useAuthStore } from "@/features/auth/hooks/use-auth";

const DashboardPage = lazy(async () => {
  const module = await import("@/features/dashboard/components/dashboard-page");
  return { default: module.DashboardPage };
});
const AccountsPage = lazy(async () => {
  const module = await import("@/features/accounts/components/accounts-page");
  return { default: module.AccountsPage };
});
const SettingsPage = lazy(async () => {
  const module = await import("@/features/settings/components/settings-page");
  return { default: module.SettingsPage };
});

function RouteLoadingFallback() {
  return (
    <div className="flex items-center justify-center py-16">
      <SpinnerBlock />
    </div>
  );
}

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
            <Route
              path="/dashboard"
              element={
                <Suspense fallback={<RouteLoadingFallback />}>
                  <DashboardPage />
                </Suspense>
              }
            />
            <Route
              path="/accounts"
              element={
                <Suspense fallback={<RouteLoadingFallback />}>
                  <AccountsPage />
                </Suspense>
              }
            />
            <Route
              path="/settings"
              element={
                <Suspense fallback={<RouteLoadingFallback />}>
                  <SettingsPage />
                </Suspense>
              }
            />
          </Route>
        </Routes>
      </AuthGate>
    </TooltipProvider>
  );
}
