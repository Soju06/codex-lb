import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ImportSettings } from "@/features/settings/components/import-settings";
import { createDashboardSettings } from "@/test/mocks/factories";

describe("ImportSettings", () => {
  it("renders the import-without-overwrite control with descriptive copy", () => {
    render(
      <ImportSettings
        settings={createDashboardSettings()}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByText("Import")).toBeInTheDocument();
    expect(screen.getByText("Allow import without overwrite")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Keep separate workspace or unknown credential slots instead of replacing by email.",
      ),
    ).toBeInTheDocument();
  });

  it("reflects settings.importWithoutOverwrite in the switch checked state", () => {
    const { rerender } = render(
      <ImportSettings
        settings={createDashboardSettings({ importWithoutOverwrite: false })}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole("switch")).not.toBeChecked();

    rerender(
      <ImportSettings
        settings={createDashboardSettings({ importWithoutOverwrite: true })}
        busy={false}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole("switch")).toBeChecked();
  });

  it("toggling the switch calls onSave with a payload where only importWithoutOverwrite changes", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const settings = createDashboardSettings({ importWithoutOverwrite: false });

    render(<ImportSettings settings={settings} busy={false} onSave={onSave} />);

    await user.click(screen.getByRole("switch"));

    expect(onSave).toHaveBeenCalledTimes(1);
    const payload = onSave.mock.calls[0][0];

    expect(payload.importWithoutOverwrite).toBe(true);

    expect(payload).toStrictEqual({ importWithoutOverwrite: true });
  });

  it("disables the switch when busy is true", () => {
    render(
      <ImportSettings
        settings={createDashboardSettings()}
        busy={true}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole("switch")).toBeDisabled();
  });
});
