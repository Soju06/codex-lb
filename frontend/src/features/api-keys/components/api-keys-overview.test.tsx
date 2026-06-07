import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createApiKey } from "@/test/mocks/factories";
import { renderWithProviders } from "@/test/utils";

import { ApiKeysOverview } from "./api-keys-overview";

describe("ApiKeysOverview", () => {
  it("summarizes the full key set and breaks usage down by metric", () => {
    renderWithProviders(
      <ApiKeysOverview
        apiKeys={[
          createApiKey({
            id: "key_1",
            name: "Primary key",
            keyPrefix: "sk-primary",
            usageSummary: {
              requestCount: 300,
              totalTokens: 80_000,
              cachedInputTokens: 12_000,
              totalCostUsd: 2.5,
            },
          }),
          createApiKey({
            id: "key_2",
            name: "Secondary key",
            keyPrefix: "sk-secondary",
            isActive: false,
            usageSummary: {
              requestCount: 120,
              totalTokens: 20_000,
              cachedInputTokens: 2_000,
              totalCostUsd: 1.0,
            },
          }),
        ]}
      />,
    );

    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getByTestId("api-keys-overview-stat-api-keys")).toHaveTextContent("2");
    expect(screen.getByTestId("api-keys-overview-stat-active-keys")).toHaveTextContent("1");
    expect(screen.getByTestId("api-keys-overview-stat-used-keys")).toHaveTextContent("2");
    expect(screen.getByTestId("api-keys-overview-stat-lifetime-requests")).toHaveTextContent("420");
    expect(screen.getByTestId("api-keys-overview-stat-lifetime-cost")).toHaveTextContent("$3.50");

    expect(screen.getByText("Lifetime Cost by API Key")).toBeInTheDocument();
    expect(screen.getByText("Lifetime Tokens by API Key")).toBeInTheDocument();

    const costPanel = screen.getByTestId("api-keys-overview-cost-panel");
    expect(within(costPanel).getByText("Primary key")).toBeInTheDocument();
    expect(within(costPanel).getByText("Secondary key")).toBeInTheDocument();
  });
});
