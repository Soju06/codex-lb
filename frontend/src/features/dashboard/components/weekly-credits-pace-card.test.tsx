import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WeeklyCreditsPaceCard } from "@/features/dashboard/components/weekly-credits-pace-card";
import type { WeeklyCreditPace } from "@/features/dashboard/utils";

const BASE_PACE: WeeklyCreditPace = {
  totalFullCredits: 1_000_000,
  totalActualRemainingCredits: 500_000,
  totalExpectedRemainingCredits: 860_000,
  actualUsedPercent: 50,
  scheduledUsedPercent: 14,
  deltaPercent: 36,
  overPlanCredits: 360_000,
  pauseForBreakEvenHours: 60.5,
  paceMultiplier: 50 / 14,
  throttleToPercent: 28,
  reduceByPercent: 72,
  proAccountEquivalentToCoverOverPlan: 360_000 / 50_400,
  proAccountsToCoverOverPlan: 8,
  status: "danger",
  accountCount: 2,
};

describe("WeeklyCreditsPaceCard", () => {
  it("renders weekly pace percentages and over-plan credits", () => {
    render(<WeeklyCreditsPaceCard pace={BASE_PACE} />);

    expect(screen.getByText("Weekly credits pace")).toBeInTheDocument();
    expect(screen.queryByText("2 accounts with weekly timing")).not.toBeInTheDocument();
    expect(screen.getByText("Used now")).toBeInTheDocument();
    expect(screen.getByText("Planned by now")).toBeInTheDocument();
    expect(screen.getByText("Over plan")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(screen.getByText("14%")).toBeInTheDocument();
    expect(screen.getByText("36 pp too fast")).toBeInTheDocument();
    expect(screen.getByText("Recovery options")).toBeInTheDocument();
    expect(screen.getByText("Pause")).toBeInTheDocument();
    expect(screen.getByText("2d 12h to break even")).toBeInTheDocument();
    expect(screen.getByText("Throttle")).toBeInTheDocument();
    expect(screen.getByText("~72% less parallel weekly-credit load")).toBeInTheDocument();
    expect(screen.getByText("Add capacity")).toBeInTheDocument();
    expect(screen.getByText("7.1x Pro weekly pool (~8 accounts)")).toBeInTheDocument();
    expect(screen.getByText("360K credits over plan")).toBeInTheDocument();
    expect(screen.getByText(/500K left vs 860K scheduled/)).toBeInTheDocument();
  });

  it("shows that no pause is needed when pace is under plan", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...BASE_PACE,
          deltaPercent: -8,
          overPlanCredits: -80_000,
          pauseForBreakEvenHours: null,
          paceMultiplier: null,
          throttleToPercent: null,
          reduceByPercent: null,
          proAccountEquivalentToCoverOverPlan: null,
          proAccountsToCoverOverPlan: null,
          status: "behind",
        }}
      />,
    );

    expect(screen.getByText("No pause needed")).toBeInTheDocument();
    expect(screen.getByText("80K credits under plan")).toBeInTheDocument();
  });

  it("shows fractional pro account capacity before the rounded account count", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...BASE_PACE,
          overPlanCredits: 26_750,
          proAccountEquivalentToCoverOverPlan: 26_750 / 50_400,
          proAccountsToCoverOverPlan: 1,
        }}
      />,
    );

    expect(screen.getByText("0.53x Pro weekly pool (~1 account)")).toBeInTheDocument();
  });

  it("does not render fake pace when data is unavailable", () => {
    const { container } = render(<WeeklyCreditsPaceCard pace={null} />);

    expect(container).toBeEmptyDOMElement();
  });
});
