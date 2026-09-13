import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { TooltipProvider } from "@/components/ui/tooltip";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import {
  ADMIN_PERMISSIONS,
  ASSIGNABLE_ROLE_IDS,
  createAccessSummary,
  createDashboardSettings,
  createSessionUser,
} from "@/test/mocks/factories";

export const settings = createDashboardSettings();

export function LocationProbe() {
  const { pathname, hash } = useLocation();
  return <div data-testid="location">{`${pathname}${hash}`}</div>;
}

/** A signed-in admin on a three-account team; tests override what they need. */
export function signInAsTeamAdmin(overrides: Partial<ReturnType<typeof useAuthStore.getState>> = {}) {
  useAuthStore.setState({
    authenticated: true,
    authMode: "standard",
    canWrite: true,
    permissions: ADMIN_PERMISSIONS,
    passwordRequired: true,
    passwordManagementEnabled: true,
    passwordSessionActive: true,
    user: createSessionUser(),
    accessSummary: createAccessSummary({ usersTotal: 3, usersInvited: 1, pendingInvites: 1, nonAdminUsers: 2 }),
    assignableRoleIds: ASSIGNABLE_ROLE_IDS,
    tier: "team",
    totpRequiredOnLogin: false,
    refreshSession: async () => useAuthStore.getState() as never,
    ...overrides,
  });
}

export function renderAt(ui: ReactElement, initialEntry = "/settings") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="*" element={ui} />
          </Routes>
          <LocationProbe />
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}
