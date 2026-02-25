import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ImportDialog } from "@/features/accounts/components/import-dialog";

describe("ImportDialog", () => {
  it("submits credentials text in credentials mode", async () => {
    const user = userEvent.setup();
    const onImportAuthJson = vi.fn().mockResolvedValue(undefined);
    const onImportCredentials = vi.fn().mockResolvedValue(undefined);

    render(
      <ImportDialog
        open
        busy={false}
        error={null}
        onOpenChange={() => {}}
        onImportAuthJson={onImportAuthJson}
        onImportCredentials={onImportCredentials}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Credentials" }));
    await user.type(
      screen.getByLabelText("Credentials"),
      "alpha@example.com:pass:JBSWY3DPEHPK3PXP",
    );
    await user.click(screen.getByRole("button", { name: "Import" }));

    expect(onImportCredentials).toHaveBeenCalledWith(
      "alpha@example.com:pass:JBSWY3DPEHPK3PXP",
    );
    expect(onImportAuthJson).not.toHaveBeenCalled();
  });

  it("submits selected auth.json file in file mode", async () => {
    const user = userEvent.setup();
    const onImportAuthJson = vi.fn().mockResolvedValue(undefined);
    const onImportCredentials = vi.fn().mockResolvedValue(undefined);

    render(
      <ImportDialog
        open
        busy={false}
        error={null}
        onOpenChange={() => {}}
        onImportAuthJson={onImportAuthJson}
        onImportCredentials={onImportCredentials}
      />,
    );

    const fileInput = screen.getByLabelText("File");
    const file = new File(["{}"], "auth.json", { type: "application/json" });
    await user.upload(fileInput, file);
    await user.click(screen.getByRole("button", { name: "Import" }));

    expect(onImportAuthJson).toHaveBeenCalledTimes(1);
    expect(onImportAuthJson.mock.calls[0]?.[0]).toBe(file);
    expect(onImportCredentials).not.toHaveBeenCalled();
  });
});
