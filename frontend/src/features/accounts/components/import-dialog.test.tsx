import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ImportDialog } from "@/features/accounts/components/import-dialog";

describe("ImportDialog", () => {
  it("requires a valid proxy when the import proxy section is open", async () => {
    const user = userEvent.setup();
    const file = new File(["{}"], "auth.json", { type: "application/json" });
    const onImport = vi.fn().mockResolvedValue({
      accountId: "acc_imported",
      email: "imported@example.com",
      planType: "plus",
      status: "active",
    });

    render(
      <ImportDialog
        open
        busy={false}
        error={null}
        onOpenChange={vi.fn()}
        onImport={onImport}
      />,
    );

    await user.upload(screen.getByLabelText("File"), file);
    await user.click(screen.getByRole("button", { name: /Configure egress proxy/ }));

    expect(screen.getByRole("button", { name: "Import & validate proxy" })).toBeDisabled();

    await user.type(screen.getByLabelText("Host"), "proxy.example.com");
    const submit = screen.getByRole("button", { name: "Import & validate proxy" });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.submit(submit.closest("form")!);

    await waitFor(() => expect(onImport).toHaveBeenCalledTimes(1));
    expect(onImport).toHaveBeenCalledWith(
      file,
      expect.objectContaining({
        host: "proxy.example.com",
        port: 1080,
        remoteDns: true,
      }),
    );
  });
});
