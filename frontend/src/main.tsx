import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App.tsx";
import { installAccessCacheEviction } from "@/features/auth/access-cache-eviction";
import { closeFlowWindowOnReturn } from "@/features/auth/oidc-window";
import { useDashboardPreferencesStore } from "@/hooks/use-dashboard-preferences";
import { queryClient } from "@/lib/query-client";
import { useThemeStore } from "@/hooks/use-theme";
import { installExternalDomMutationGuard } from "@/utils/external-dom-mutation-guard";
import "@/i18n";

import "./index.css";

// A sign-in flow window coming back says so — to the page that opened it, and
// on a same-origin channel for the identity providers whose opener policy takes
// that page away — before any of this mounts: it is a round trip, not a second
// dashboard. Only when it still has an opener can it close itself, so that is
// the one case it keeps the document to itself.
if (!closeFlowWindowOnReturn()) {
  installExternalDomMutationGuard();
  installAccessCacheEviction();
  useThemeStore.getState().initializeTheme();
  useDashboardPreferencesStore.getState().initializePreferences();

  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}
