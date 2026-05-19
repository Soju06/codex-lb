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
  scheduleGapCredits: 360_000,
  overPlanCredits: 360_000,
  projectedShortfallCredits: 360_000,
  pauseForBreakEvenHours: 60.5,
  paceMultiplier: 50 / 14,
  throttleToPercent: 28,
  reduceByPercent: 72,
  proAccountEquivalentToCoverOverPlan: 360_000 / 50_400,
  proAccountsToCoverOverPlan: 8,
  projectedDepletionHours: 8,
  projectedMinimumRemainingCredits: 0,
  forecastBurnRateCreditsPerHour: 12_000,
  scheduledBurnRateCreditsPerHour: 3_000,
  status: "danger",
  accountCount: 2,
  staleAccountCount: 0,
  inactiveAccountCount: 0,
  confidence: "high",
};

describe("WeeklyCreditsPaceCard", () => {
  it("renders weekly pace percentages and separates schedule gap from forecast shortfall", () => {
    render(<WeeklyCreditsPaceCard pace={BASE_PACE} />);

    expect(screen.getByText("Weekly credits pace")).toBeInTheDocument();
    expect(screen.queryByText("2 accounts with weekly timing")).not.toBeInTheDocument();
    expect(screen.getByText("Used now")).toBeInTheDocument();
    expect(screen.getByText("Scheduled by now")).toBeInTheDocument();
    expect(screen.getByText("Pace gap")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(screen.getByText("14%")).toBeInTheDocument();
    expect(screen.getByText("3.57x recent/scheduled")).toBeInTheDocument();
    expect(screen.getByText("Recovery options")).toBeInTheDocument();
    expect(screen.getByText("Pause")).toBeInTheDocument();
    expect(screen.getByText("2d 12h until reset")).toBeInTheDocument();
    expect(screen.getByText("Throttle")).toBeInTheDocument();
    expect(screen.getByText("Reduce ongoing weekly-credit load by ~72%")).toBeInTheDocument();
    expect(screen.getByText("Add capacity")).toBeInTheDocument();
    expect(screen.getByText("7.1x Pro weekly pool (~8 accounts)")).toBeInTheDocument();
    expect(screen.getByText("360K credits behind schedule now")).toBeInTheDocument();
    expect(screen.getByText("360K credits projected short before reset")).toBeInTheDocument();
    expect(screen.queryByText("500K")).not.toBeInTheDocument();
    expect(screen.getByText("Schedule marker")).toBeInTheDocument();
  });

  it("hides recovery options when the pool is on the safe side of schedule", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...BASE_PACE,
          deltaPercent: -8,
          scheduleGapCredits: 0,
          overPlanCredits: 0,
          projectedShortfallCredits: 0,
          pauseForBreakEvenHours: null,
          paceMultiplier: null,
          throttleToPercent: null,
          reduceByPercent: null,
          proAccountEquivalentToCoverOverPlan: null,
          proAccountsToCoverOverPlan: null,
          projectedMinimumRemainingCredits: 80_000,
          forecastBurnRateCreditsPerHour: 0,
          status: "behind",
        }}
      />,
    );

    expect(screen.queryByText("Recovery options")).not.toBeInTheDocument();
    expect(screen.queryByText("No pause needed")).not.toBeInTheDocument();
    expect(screen.getByText("8% below schedule")).toBeInTheDocument();
    expect(screen.queryByText("80K credits projected low-water mark")).not.toBeInTheDocument();
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

  it("shows schedule gap without recovery when recent forecast is safe", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...BASE_PACE,
          scheduleGapCredits: 3_096,
          overPlanCredits: 3_096,
          projectedShortfallCredits: 0,
          pauseForBreakEvenHours: null,
          paceMultiplier: 0,
          throttleToPercent: null,
          reduceByPercent: null,
          proAccountEquivalentToCoverOverPlan: null,
          proAccountsToCoverOverPlan: null,
          forecastBurnRateCreditsPerHour: 0,
          status: "ahead",
        }}
      />,
    );

    expect(screen.queryByText("Pause")).not.toBeInTheDocument();
    expect(screen.queryByText("Throttle")).not.toBeInTheDocument();
    expect(screen.getByText("3.1K credits behind schedule now")).toBeInTheDocument();
    expect(screen.getByText("No weekly shortfall projected at recent pace")).toBeInTheDocument();
  });

  it("does not render fake pace when data is unavailable", () => {
    const { container } = render(<WeeklyCreditsPaceCard pace={null} />);

    expect(container).toBeEmptyDOMElement();
  });
});
