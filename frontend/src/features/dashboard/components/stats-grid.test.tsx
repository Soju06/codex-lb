import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatsGrid } from "@/features/dashboard/components/stats-grid";

describe("StatsGrid", () => {
  it("renders four metric cards with values", () => {
    render(
      <StatsGrid
        stats={[
          { label: "Requests (7d)", value: "228" },
          { label: "Tokens (7d)", value: "45K" },
          { label: "Cost (7d)", value: "$1.82", meta: "Avg/hr $0.01" },
          { label: "Error rate", value: "2.8%", meta: "Top: rate_limit_exceeded" },
        ]}
      />,
    );

    expect(screen.getByText("Requests (7d)")).toBeInTheDocument();
    expect(screen.getByText("228")).toBeInTheDocument();
    expect(screen.getByText("Tokens (7d)")).toBeInTheDocument();
    expect(screen.getByText("45K")).toBeInTheDocument();
    expect(screen.getByText("Cost (7d)")).toBeInTheDocument();
    expect(screen.getByText("Avg/hr $0.01")).toBeInTheDocument();
    expect(screen.getByText("Error rate")).toBeInTheDocument();
    expect(screen.getByText("Top: rate_limit_exceeded")).toBeInTheDocument();
  });
});
