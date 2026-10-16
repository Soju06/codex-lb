import { lazy, Suspense, useState } from "react";
import { matchPath, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";

import { AppHeader } from "@/components/layout/app-header";
import { routePermission } from "@/components/layout/nav-items";
import {
  NotFoundPage,
  RouteErrorBoundary,
  RouteLoading,
} from "@/components/layout/route-recovery";
import { RouteScrollRestoration } from "@/components/layout/route-scroll-restoration";
import {
  STATUS_BAR_DEFAULT_HEIGHT_PX,
  StatusBar,
} from "@/components/layout/status-bar";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthGate } from "@/features/auth/components/auth-gate";
import { StepUpDialog } from "@/features/auth/components/step-up-dialog";
import { hasPermission, useAuthStore } from "@/features/auth/hooks/use-auth";
import { signedInLoginDestination } from "@/features/auth/oidc-window";
import { TelemetryConsentDialog } from "@/features/settings/components/telemetry-consent-dialog";
import { useTimeFormatStore } from "@/hooks/use-time-format";
import { cn } from "@/lib/utils";

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
const AccessPage = lazy(() =>
  import("@/features/settings/components/access/access-page").then((m) => ({ default: m.AccessPage })),
);

// Route guard: a page whose nav item the session cannot use is not rendered.
// `/dashboard` is the fallback (every assignable role holds `dashboard:read`);
// when even that is missing the not-found surface renders instead of looping.
function RouteGuard() {
  const { pathname } = useLocation();
  const permissions = useAuthStore((state) => state.permissions);
  const required = routePermission(pathname);
  if (required !== null && !hasPermission(permissions, required)) {
    return pathname === "/dashboard" ? <NotFoundPage /> : <Navigate to="/dashboard" replace />;
  }
  return <Outlet />;
}

function AppLayout() {
  const { hash, key: locationKey, pathname, search } = useLocation();
  const fullscreenAccounts = matchPath("/dashboard", pathname) !== null && new URLSearchParams(search).get("accountsFullscreen") === "1";
  const logout = useAuthStore((state) => state.logout);
  const passwordRequired = useAuthStore((state) => state.passwordRequired);
  const role = useAuthStore((state) => state.role);
  const guestPasswordRequired = useAuthStore((state) => state.guestPasswordRequired);
  const startAdminLogin = useAuthStore((state) => state.startAdminLogin);
  const timeFormat = useTimeFormatStore((state) => state.timeFormat);
  const isGuest = role === "guest";
  const [statusBarHeight, setStatusBarHeight] = useState(STATUS_BAR_DEFAULT_HEIGHT_PX);

  return (
    <div
      className={cn("flex flex-col bg-background", fullscreenAccounts ? "isolate h-dvh overflow-hidden" : "min-h-screen")}
      data-time-format={timeFormat}
      data-dashboard-shell
      style={{ paddingBottom: statusBarHeight }}
    >
      <RouteScrollRestoration />
      <AppHeader
        className="shrink-0"
        onLogout={() => {
          void logout();
        }}
        onAdminLogin={startAdminLogin}
        showAdminLogin={isGuest && passwordRequired}
        showLogout={(role === "admin" && passwordRequired) || (isGuest && guestPasswordRequired)}
      />
      <main className={cn("mx-auto flex w-full max-w-375 flex-1 flex-col px-4 pb-8 sm:px-6", fullscreenAccounts ? "min-h-0" : "pt-8")}>
        <RouteErrorBoundary
          key={pathname}
          resetKey={`${locationKey}:${pathname}${search}${hash}`}
        >
          <Suspense fallback={<RouteLoading />}>
            <Outlet />
          </Suspense>
        </RouteErrorBoundary>
      </main>
      <StatusBar onHeightChange={setStatusBarHeight} />
      <TelemetryConsentDialog />
    </div>
  );
}

export default function App() {
  return (
    <TooltipProvider>
      <Toaster richColors />
      <StepUpDialog />
      <AuthGate>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            {/* Public pending screen: once the account exists, "Try again" lands in the app. */}
            <Route path="/auth/pending" element={<Navigate to="/dashboard" replace />} />
            {/* Public login route (`?local=1` is the break-glass form): a session
                that already exists belongs in the app, not on a not-found page —
                and, when a same-tab sign-in flow it started failed here, back on
                the card that started it rather than on the dashboard. */}
            <Route path="/login" element={<Navigate to={signedInLoginDestination()} replace />} />
            <Route element={<RouteGuard />}>
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/reports" element={<ReportsPage />} />
              <Route path="/accounts" element={<AccountsPage />} />
              <Route path="/automations" element={<AutomationsPage />} />
              <Route path="/apis" element={<ApisPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/settings/access" element={<AccessPage />} />
            </Route>
            <Route path="/firewall" element={<Navigate to="/settings?advanced=1#firewall" replace />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </AuthGate>
    </TooltipProvider>
  );
}
