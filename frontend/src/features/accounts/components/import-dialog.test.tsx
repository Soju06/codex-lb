import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ImportDialog } from "@/features/accounts/components/import-dialog";

function deferred(): {
  promise: Promise<void>;
  resolve: () => void;
} {
  let resolve: (() => void) | undefined;
  const promise = new Promise<void>((resolvePromise) => {
    resolve = resolvePromise;
  });
  if (resolve === undefined) {
    throw new Error("deferred executor did not run");
  }
  return { promise, resolve };
}

describe("ImportDialog", () => {
  it("imports a multi-file selection sequentially and resets after success", async () => {
    const user = userEvent.setup();
    const firstImport = deferred();
    const firstFile = new File(["{}"], "first.json", { type: "application/json" });
    const secondFile = new File(["{}"], "second.json", { type: "application/json" });
    const onImport = vi
      .fn<(file: File) => Promise<void>>()
      .mockImplementationOnce(() => firstImport.promise)
      .mockResolvedValueOnce(undefined);
    const onOpenChange = vi.fn();

    render(
      <ImportDialog
        open
        busy={false}
        error={null}
        onOpenChange={onOpenChange}
        onImport={onImport}
      />,
    );

    const input = screen.getByLabelText(/auth\.json file/i);
    expect(input).toHaveAttribute("multiple");
    await user.upload(input, [firstFile, secondFile]);

    expect(screen.getByText("first.json")).toBeInTheDocument();
    expect(screen.getByText("second.json")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Import" }));

    await waitFor(() => expect(onImport).toHaveBeenCalledTimes(1));
    expect(onImport).toHaveBeenNthCalledWith(1, firstFile);
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();

    firstImport.resolve();

    await waitFor(() => expect(onImport).toHaveBeenCalledTimes(2));
    expect(onImport).toHaveBeenNthCalledWith(2, secondFile);
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(screen.queryByText("first.json")).not.toBeInTheDocument();
    expect(screen.queryByText("second.json")).not.toBeInTheDocument();
  });

  it("stops on failure and retries only the failed and unattempted files", async () => {
    const user = userEvent.setup();
    const files = [
      new File(["{}"], "succeeded.json", { type: "application/json" }),
      new File(["{}"], "failed.json", { type: "application/json" }),
      new File(["{}"], "unattempted.json", { type: "application/json" }),
    ];
    let failedAttempts = 0;
    const onImport = vi.fn(async (file: File) => {
      if (file.name === "failed.json" && failedAttempts === 0) {
        failedAttempts += 1;
        throw new Error("Invalid auth file");
      }
    });
    const onOpenChange = vi.fn();

    render(
      <ImportDialog
        open
        busy={false}
        error="Invalid auth file"
        onOpenChange={onOpenChange}
        onImport={onImport}
      />,
    );

    await user.upload(screen.getByLabelText(/auth\.json file/i), files);
    await user.click(screen.getByRole("button", { name: "Import" }));

    await waitFor(() => expect(onImport).toHaveBeenCalledTimes(2));
    expect(onImport.mock.calls.map(([file]) => file.name)).toEqual([
      "succeeded.json",
      "failed.json",
    ]);
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(screen.queryByText("succeeded.json")).not.toBeInTheDocument();
    expect(screen.getByText("failed.json")).toBeInTheDocument();
    expect(screen.getByText("unattempted.json")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Import" }));

    await waitFor(() => expect(onImport).toHaveBeenCalledTimes(4));
    expect(onImport.mock.calls.map(([file]) => file.name)).toEqual([
      "succeeded.json",
      "failed.json",
      "failed.json",
      "unattempted.json",
    ]);
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });
});
