import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
