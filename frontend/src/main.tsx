import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App.tsx";
import { useDashboardPreferencesStore } from "@/hooks/use-dashboard-preferences";
import { queryClient } from "@/lib/query-client";
import { useThemeStore } from "@/hooks/use-theme";
import { installExternalDomMutationGuard } from "@/utils/external-dom-mutation-guard";

import "./index.css";

installExternalDomMutationGuard();
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
)
