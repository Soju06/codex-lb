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
const UsersPage = lazy(async () => {
  const module = await import("@/features/users/components/users-page");
  return { default: module.UsersPage };
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
  const role = useAuthStore((state) => state.user?.role);
  const isAdmin = role === "admin";
  const navItems = isAdmin
    ? [
        { to: "/dashboard", label: "Dashboard" },
        { to: "/accounts", label: "Accounts" },
        { to: "/users", label: "Users" },
        { to: "/settings", label: "Settings" },
      ]
    : [
        { to: "/dashboard", label: "Dashboard" },
        { to: "/accounts", label: "Accounts" },
        { to: "/settings", label: "Settings" },
      ];

  return (
    <div className="flex min-h-screen flex-col bg-background pb-10">
      <AppHeader
        onLogout={() => {
          void logout();
        }}
        navItems={navItems}
        showLogout
      />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">
        <Outlet />
      </main>
      <StatusBar />
    </div>
  );
}

export default function App() {
  const initialized = useAuthStore((state) => state.initialized);
  const role = useAuthStore((state) => state.user?.role);
  const isAdmin = role === "admin";

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
              path="/users"
              element={
                !initialized ? (
                  <RouteLoadingFallback />
                ) : isAdmin ? (
                  <Suspense fallback={<RouteLoadingFallback />}>
                    <UsersPage />
                  </Suspense>
                ) : (
                  <Navigate to="/dashboard" replace />
                )
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
