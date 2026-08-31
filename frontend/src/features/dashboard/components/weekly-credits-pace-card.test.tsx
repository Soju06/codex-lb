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
  smoothedDeltaPercent: 24,
  smoothedScheduleGapCredits: 240_000,
  paceGapSmoothingMinutes: 30,
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
    expect(screen.getByText("24% over planned usage")).toBeInTheDocument();
    expect(screen.getByText("Recommendations")).toBeInTheDocument();
    expect(screen.getByText("Pause")).toBeInTheDocument();
    expect(screen.getByText("2d 12h until reset")).toBeInTheDocument();
    expect(screen.getByText("Throttle")).toBeInTheDocument();
    expect(screen.getByText("Reduce ongoing weekly-credit load by ~72%")).toBeInTheDocument();
    expect(screen.getByText("Add capacity")).toBeInTheDocument();
    expect(screen.getByText("7.1x Pro weekly pool (~8 accounts)")).toBeInTheDocument();
    expect(screen.getByText("240K credits over planned usage over 30m")).toBeInTheDocument();
    expect(screen.getByText("360K credits projected short before reset")).toBeInTheDocument();
    expect(screen.queryByText("500K")).not.toBeInTheDocument();
    expect(screen.getByText("Schedule marker")).toBeInTheDocument();
  });

  it("hides recommendations when the pool is on the safe side of schedule", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...BASE_PACE,
          deltaPercent: -8,
          scheduleGapCredits: 0,
          smoothedDeltaPercent: -8,
          smoothedScheduleGapCredits: 0,
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

    expect(screen.queryByText("Recommendations")).not.toBeInTheDocument();
    expect(screen.queryByText("No pause needed")).not.toBeInTheDocument();
    expect(screen.getByText("8% below planned usage")).toBeInTheDocument();
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

  it("shows recommendations for a current schedule gap even when recent forecast is safe", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...BASE_PACE,
          scheduleGapCredits: 3_096,
          smoothedDeltaPercent: 36,
          smoothedScheduleGapCredits: 3_096,
          paceGapSmoothingMinutes: 0,
          overPlanCredits: 3_096,
          projectedShortfallCredits: 0,
          pauseForBreakEvenHours: null,
          paceMultiplier: 0,
          throttleToPercent: null,
          reduceByPercent: null,
          proAccountEquivalentToCoverOverPlan: null,
          proAccountsToCoverOverPlan: null,
          forecastBurnRateCreditsPerHour: 0,
          scheduledBurnRateCreditsPerHour: 1_032,
          status: "ahead",
        }}
      />,
    );

    expect(screen.getByText("Recommendations")).toBeInTheDocument();
    expect(screen.queryByText("Pause")).not.toBeInTheDocument();
    expect(screen.queryByText("3h to return to schedule")).not.toBeInTheDocument();
    expect(screen.queryByText("Throttle")).not.toBeInTheDocument();
    expect(screen.getByText("Add capacity")).toBeInTheDocument();
    expect(screen.getByText("0.061x Pro weekly pool (~1 account)")).toBeInTheDocument();
    expect(screen.getByText("36% over planned usage")).toBeInTheDocument();
    expect(screen.getByText("3.1K credits over planned usage now")).toBeInTheDocument();
    expect(screen.getByText("No weekly shortfall projected at recent pace")).toBeInTheDocument();
  });

  it("keeps a danger label when recent burn projects a shortfall below schedule", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...BASE_PACE,
          deltaPercent: -5,
          scheduleGapCredits: 0,
          smoothedDeltaPercent: -5,
          smoothedScheduleGapCredits: 0,
          overPlanCredits: 0,
          projectedShortfallCredits: 42_000,
          status: "danger",
        }}
      />,
    );

    expect(screen.getByText("Recent burn shortfall")).toBeInTheDocument();
    expect(screen.queryByText("5% below planned usage")).not.toBeInTheDocument();
    expect(screen.getByText("42K credits projected short before reset")).toBeInTheDocument();
  });

  it("does not render fake pace when data is unavailable", () => {
    const { container } = render(<WeeklyCreditsPaceCard pace={null} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("falls back to the legacy layout when runwayStatus is absent (old backend)", () => {
    render(<WeeklyCreditsPaceCard pace={BASE_PACE} />);

    expect(screen.getByText("Used now")).toBeInTheDocument();
    expect(screen.getByText("Scheduled by now")).toBeInTheDocument();
    expect(screen.queryByTestId("weekly-runway-verdict")).not.toBeInTheDocument();
    expect(screen.queryByTestId("runway-timeline")).not.toBeInTheDocument();
  });
});

const RUNWAY_PACE: WeeklyCreditPace = {
  ...BASE_PACE,
  actualUsedPercent: 58,
  scheduledUsedPercent: 40,
  deltaPercent: 18,
  scheduleGapCredits: 0,
  smoothedDeltaPercent: 18,
  smoothedScheduleGapCredits: 0,
  overPlanCredits: 0,
  projectedShortfallCredits: 0,
  pauseForBreakEvenHours: null,
  paceMultiplier: null,
  throttleToPercent: null,
  reduceByPercent: null,
  proAccountEquivalentToCoverOverPlan: null,
  proAccountsToCoverOverPlan: null,
  status: "on_track",
  runwayStatus: "safe",
  headroomPercent: 42,
  headroomCredits: 420_000,
  burnRateRecentCreditsPerHour: 6_000,
  depletionEtaHours: 70,
  nextReliefInHours: 26,
  nextReliefCredits: 100_800,
  resetEvents: [
    { at: new Date(Date.now() + 26 * 3_600_000).toISOString(), creditsReturned: 100_800 },
    { at: new Date(Date.now() + 50 * 3_600_000).toISOString(), creditsReturned: 50_400 },
  ],
  saturatedAccountCount: 0,
  topApiKeys: [],
  addProAccounts: null,
};

describe("WeeklyCreditsPaceCard runway layout", () => {
  it("paints the full runway content from a single overview payload", () => {
    render(<WeeklyCreditsPaceCard pace={RUNWAY_PACE} />);

    const verdict = screen.getByTestId("weekly-runway-verdict");
    expect(verdict).toHaveTextContent("Safe");
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.getByText("420K credits left")).toBeInTheDocument();
    expect(screen.getByText("runs out in ~2d 22h")).toBeInTheDocument();
    expect(screen.getByText("next reset in 1d 2h · returns ~100.8K credits")).toBeInTheDocument();
    expect(screen.getByTestId("runway-timeline")).toBeInTheDocument();
    expect(screen.getByTestId("runway-eta-marker")).toBeInTheDocument();
    expect(screen.getAllByTestId("runway-reset-tick")).toHaveLength(2);
    expect(screen.getByText("now")).toBeInTheDocument();
  });

  it("renders a neutral verdict badge without warning emphasis when safe", () => {
    render(<WeeklyCreditsPaceCard pace={RUNWAY_PACE} />);

    const verdict = screen.getByTestId("weekly-runway-verdict");
    expect(verdict).toHaveTextContent("Safe");
    expect(verdict.className).toContain("text-muted-foreground");
    expect(verdict.className).not.toContain("amber");
    expect(verdict.className).not.toContain("red");
    expect(screen.queryByTestId("runway-recommendations")).not.toBeInTheDocument();
    expect(screen.queryByText("Recommendations")).not.toBeInTheDocument();
  });

  it("renders an amber verdict badge when tight", () => {
    render(<WeeklyCreditsPaceCard pace={{ ...RUNWAY_PACE, runwayStatus: "tight" }} />);

    const verdict = screen.getByTestId("weekly-runway-verdict");
    expect(verdict).toHaveTextContent("Tight");
    expect(verdict.className).toContain("amber");
  });

  it("pairs depletion and missed relief times with warning emphasis when runs dry", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...RUNWAY_PACE,
          runwayStatus: "runs_dry",
          headroomPercent: 8,
          headroomCredits: 48_000,
          depletionEtaHours: 8,
          nextReliefInHours: 26,
        }}
      />,
    );

    const verdict = screen.getByTestId("weekly-runway-verdict");
    expect(verdict).toHaveTextContent("Runs dry");
    expect(verdict.className).toContain("red");
    expect(screen.getByText("relief in 1d 2h — arrives after depletion")).toBeInTheDocument();
    expect(screen.queryByText("next reset in 1d 2h · returns ~100.8K credits")).not.toBeInTheDocument();
    expect(screen.getByText("8.0%")).toBeInTheDocument();
  });

  it("shows throttle guidance before add-capacity when runs dry", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...RUNWAY_PACE,
          runwayStatus: "runs_dry",
          depletionEtaHours: 8,
          throttleToPercent: 28,
          addProAccounts: 2,
        }}
      />,
    );

    const recommendations = screen.getByTestId("runway-recommendations");
    const text = recommendations.textContent ?? "";
    expect(text).toContain("~28% of current load");
    expect(text).toContain("Add 2 Pro accounts");
    expect(text.indexOf("~28% of current load")).toBeLessThan(text.indexOf("Add 2 Pro accounts"));
  });

  it("hides throttle guidance outside runs_dry but keeps a gated add-capacity line", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...RUNWAY_PACE,
          runwayStatus: "tight",
          throttleToPercent: 28,
          addProAccounts: 1,
        }}
      />,
    );

    const recommendations = screen.getByTestId("runway-recommendations");
    expect(recommendations.textContent).not.toContain("Throttle");
    expect(recommendations.textContent).toContain("Add 1 Pro account");
  });

  it("renders the per-key attribution list when present", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...RUNWAY_PACE,
          topApiKeys: [
            {
              apiKeyId: "key_hermes_prod",
              name: "hermes-prod",
              requests: 12_400,
              billableTokens: 9_800_000,
              cachedTokens: 4_000_000,
              dominantModel: "gpt-5.2-codex",
            },
            {
              apiKeyId: "key_batch_eval",
              name: "batch-eval",
              requests: 800,
              billableTokens: 14_200_000,
              cachedTokens: 0,
              dominantModel: "gpt-5.2",
            },
          ],
        }}
      />,
    );

    expect(screen.getByTestId("runway-attribution")).toBeInTheDocument();
    expect(screen.getByText("hermes-prod")).toBeInTheDocument();
    expect(screen.getByText("12.4K req")).toBeInTheDocument();
    expect(screen.getByText("9.8M tok")).toBeInTheDocument();
    expect(screen.getByText("gpt-5.2-codex")).toBeInTheDocument();
    expect(screen.getByText("batch-eval")).toBeInTheDocument();
  });

  it("hides the attribution list when no keys are reported", () => {
    render(<WeeklyCreditsPaceCard pace={{ ...RUNWAY_PACE, topApiKeys: [] }} />);

    expect(screen.queryByTestId("runway-attribution")).not.toBeInTheDocument();
  });

  it("renders attribution rows with colliding key names", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...RUNWAY_PACE,
          topApiKeys: [
            { name: "(unnamed)", requests: 500, billableTokens: 1_000_000, cachedTokens: 0, dominantModel: "gpt-5.2" },
            { name: "(unnamed)", requests: 300, billableTokens: 2_000_000, cachedTokens: 0, dominantModel: "gpt-5.2-codex" },
          ],
        }}
      />,
    );

    expect(screen.getAllByText("(unnamed)")).toHaveLength(2);
    expect(screen.getByText("500 req")).toBeInTheDocument();
    expect(screen.getByText("300 req")).toBeInTheDocument();
  });

  it("stretches the timeline horizon so reset events past 48h are not dropped", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...RUNWAY_PACE,
          depletionEtaHours: 8,
          nextReliefInHours: 12,
          resetEvents: [
            { at: new Date(Date.now() + 12 * 3_600_000).toISOString(), creditsReturned: 100_800 },
            { at: new Date(Date.now() + 72 * 3_600_000).toISOString(), creditsReturned: 50_400 },
          ],
        }}
      />,
    );

    expect(screen.getAllByTestId("runway-reset-tick")).toHaveLength(2);
    expect(screen.getByText("3d")).toBeInTheDocument();
  });

  it("does not crash on an ETA beyond the representable Date range", () => {
    render(<WeeklyCreditsPaceCard pace={{ ...RUNWAY_PACE, depletionEtaHours: 3e9 }} />);

    expect(screen.getByTestId("runway-timeline")).toBeInTheDocument();
    expect(screen.getByTestId("runway-eta-marker")).not.toHaveAttribute("title");
    expect(screen.getByText(/runs out in/)).toBeInTheDocument();
  });

  it("shows steady-state copy instead of an ETA when a measured burn is zero", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...RUNWAY_PACE,
          burnRateRecentCreditsPerHour: 0,
          depletionEtaHours: null,
        }}
      />,
    );

    expect(screen.getByText("no recent burn — holding steady")).toBeInTheDocument();
    expect(screen.queryByText("not enough recent samples to measure burn")).not.toBeInTheDocument();
    expect(screen.queryByText(/runs out in/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("runway-eta-marker")).not.toBeInTheDocument();
  });

  it("explains an unmeasured burn instead of claiming the pool is holding steady", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{
          ...RUNWAY_PACE,
          burnRateRecentCreditsPerHour: null,
          depletionEtaHours: null,
        }}
      />,
    );

    expect(screen.getByText("not enough recent samples to measure burn")).toBeInTheDocument();
    expect(screen.queryByText("no recent burn — holding steady")).not.toBeInTheDocument();
    expect(screen.queryByText(/runs out in/)).not.toBeInTheDocument();
  });

  it("falls back to the legacy layout when a headroom companion is missing", () => {
    const { unmount } = render(
      <WeeklyCreditsPaceCard pace={{ ...RUNWAY_PACE, headroomPercent: undefined }} />,
    );

    expect(screen.queryByTestId("weekly-runway-verdict")).not.toBeInTheDocument();
    expect(screen.queryByTestId("runway-timeline")).not.toBeInTheDocument();
    expect(screen.getByText("Used now")).toBeInTheDocument();
    unmount();

    render(<WeeklyCreditsPaceCard pace={{ ...RUNWAY_PACE, headroomCredits: undefined }} />);

    expect(screen.queryByTestId("weekly-runway-verdict")).not.toBeInTheDocument();
    expect(screen.queryByTestId("runway-timeline")).not.toBeInTheDocument();
    expect(screen.getByText("Used now")).toBeInTheDocument();
  });

  it("labels demand figures as floors when every account is saturated", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{ ...RUNWAY_PACE, accountCount: 2, saturatedAccountCount: 2 }}
      />,
    );

    expect(screen.getByText("All accounts saturated — demand figures are at-least floors")).toBeInTheDocument();
    expect(screen.getByText(/at ≥6K\/h/)).toBeInTheDocument();
  });

  it("does not label floors while unsaturated accounts remain", () => {
    render(
      <WeeklyCreditsPaceCard
        pace={{ ...RUNWAY_PACE, accountCount: 2, saturatedAccountCount: 1 }}
      />,
    );

    expect(
      screen.queryByText("All accounts saturated — demand figures are at-least floors"),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/at ~6K\/h/)).toBeInTheDocument();
  });
});
