import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { BrowserRouter } from "react-router-dom";

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

type ProvidersProps = {
  children: ReactNode;
  queryClient: QueryClient;
};

function Providers({ children, queryClient }: ProvidersProps) {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
}

export function renderWithProviders(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
): RenderResult & { queryClient: QueryClient } {
  const queryClient = createTestQueryClient();
  const result = render(ui, {
    wrapper: ({ children }) => <Providers queryClient={queryClient}>{children}</Providers>,
    ...options,
  });
  return {
    ...result,
    queryClient,
  };
}
