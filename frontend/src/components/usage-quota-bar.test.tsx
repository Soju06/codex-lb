import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MiniQuotaBar } from "@/components/mini-quota-bar";
import { UsageQuotaBar } from "@/components/usage-quota-bar";

describe("UsageQuotaBar", () => {
  it.each([[46, 80, 26, 20], [10, 80, 0, 10], [46, null, 46, 0]])(
    "distinguishes provider remaining %s with cap %s", (percent, cap, usable, reserved) => {
      render(<UsageQuotaBar percent={percent} cap={cap} />);
      expect(screen.getByRole("img")).toHaveAccessibleName(
        percent + "% provider remaining; " + reserved + "% reserved for you · " + usable + "% available to Codex LB",
      );
    },
  );
});

it.each([46, null])("keeps both capped mini-bar windows identifiable at %s remaining", (percent) => {
  render(<><MiniQuotaBar percent={percent} cap={80} testId="five" aria-label="5-hour quota" />
    <MiniQuotaBar percent={percent} cap={80} testId="weekly" aria-label="Weekly quota" /></>);
  expect(screen.getByRole("img", { name: /^5-hour quota;/ })).toBeInTheDocument();
  expect(screen.getByRole("img", { name: /^Weekly quota;/ })).toBeInTheDocument();
});
