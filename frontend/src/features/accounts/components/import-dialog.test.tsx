import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ImportDialog } from "@/features/accounts/components/import-dialog";

describe("ImportDialog", () => {
  it("submits multiple files and closes when all imports succeed", async () => {
    const user = userEvent.setup();
    const onImport = vi.fn().mockResolvedValue({
      imported: [
        {
          filename: "one.json",
          accountId: "acc-1",
          email: "one@example.com",
          planType: "plus",
          status: "active",
          refreshedOnImport: false,
        },
      ],
      failed: [],
    });
    const onOpenChange = vi.fn();

    render(
      <ImportDialog
        open
        busy={false}
        error={null}
        result={null}
        onOpenChange={onOpenChange}
        onImport={onImport}
      />,
    );

    const files = [
      new File(["{}"], "one.json", { type: "application/json" }),
      new File(["{}"], "two.json", { type: "application/json" }),
    ];

    await user.upload(screen.getByLabelText("Files"), files);
    expect(screen.getByText("2 files selected")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Import" }));

    expect(onImport).toHaveBeenCalledWith(files);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("renders import failures returned by the batch endpoint", () => {
    render(
      <ImportDialog
        open
        busy={false}
        error={null}
        result={{
          imported: [],
          failed: [
            {
              filename: "broken.json",
              code: "invalid_auth_json",
              message: "Invalid auth.json payload",
            },
          ],
        }}
        onOpenChange={() => {}}
        onImport={vi.fn()}
      />,
    );

    expect(screen.getByText("Imported 0 files, 1 failed.")).toBeInTheDocument();
    expect(screen.getByText("broken.json:")).toBeInTheDocument();
    expect(screen.getByText(/Invalid auth\.json payload/)).toBeInTheDocument();
  });
});
