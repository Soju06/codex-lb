import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AdditionalQuotas } from "@/features/dashboard/components/additional-quotas";
import type { AdditionalQuotaView } from "@/features/dashboard/utils";

function item(overrides: Partial<AdditionalQuotaView> = {}): AdditionalQuotaView {
  return {
    limitName: "codex_other",
    displayName: "Codex Spark",
    primaryUsedPercent: 45,
    primaryResetAt: Math.floor(Date.now() / 1000) + 3600,
    primaryWindowMinutes: 300,
    secondaryUsedPercent: null,
    secondaryResetAt: null,
    secondaryWindowMinutes: null,
    ...overrides,
  };
}

describe("AdditionalQuotas", () => {
  it("renders nothing when items is empty", () => {
    const { container } = render(<AdditionalQuotas items={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("renders card with display name", () => {
    render(<AdditionalQuotas items={[item()]} />);
    expect(screen.getByText("Additional Quotas")).toBeInTheDocument();
    expect(screen.getByText("Codex Spark")).toBeInTheDocument();
  });

  it("shows progress bar with correct width for usedPercent", () => {
    render(<AdditionalQuotas items={[item({ primaryUsedPercent: 72 })]} />);
    expect(screen.getByText("72% used")).toBeInTheDocument();

    const bar = document.querySelector("[style*='width: 72%']");
    expect(bar).toBeTruthy();
  });

  it("shows secondary window when secondaryUsedPercent is present", () => {
    render(
      <AdditionalQuotas
        items={[
          item({
            secondaryUsedPercent: 30,
            secondaryResetAt: Math.floor(Date.now() / 1000) + 7200,
            secondaryWindowMinutes: 10080,
          }),
        ]}
      />,
    );

    expect(screen.getByText("45% used")).toBeInTheDocument();
    expect(screen.getByText("30% used")).toBeInTheDocument();
  });

  it("hides secondary window when secondaryUsedPercent is null", () => {
    render(<AdditionalQuotas items={[item()]} />);

    expect(screen.getByText("45% used")).toBeInTheDocument();
    const usedLabels = screen.getAllByText(/% used/);
    expect(usedLabels).toHaveLength(1);
  });


  it("treats resetAt=0 as valid (shows Resetting... not empty)", () => {
    render(
      <AdditionalQuotas
        items={[item({ primaryResetAt: 0 })]}
      />,
    );

    // resetAt=0 is a valid epoch (past) — should show "Resetting..." not be hidden
    expect(screen.getByText("Resetting...")).toBeInTheDocument();
  });

});
