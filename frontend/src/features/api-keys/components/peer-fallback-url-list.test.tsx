import { useState } from "react";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/utils";

import { PeerFallbackUrlList } from "./peer-fallback-url-list";

function ControlledPeerFallbackUrlList() {
  const [value, setValue] = useState<string[]>([]);

  return (
    <>
      <PeerFallbackUrlList value={value} onChange={setValue} />
      <output aria-label="Selected peer fallback URLs">{value.join(",")}</output>
    </>
  );
}

describe("PeerFallbackUrlList", () => {
  it("adds and removes a peer fallback URL", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ControlledPeerFallbackUrlList />);

    await user.type(screen.getByLabelText("Peer fallback base URL"), "http://127.0.0.1:2461/");
    await user.click(screen.getByRole("button", { name: "Add URL" }));

    expect(screen.getByLabelText("Selected peer fallback URLs")).toHaveTextContent("http://127.0.0.1:2461");

    await user.click(screen.getByRole("button", { name: "Remove http://127.0.0.1:2461" }));

    expect(screen.getByLabelText("Selected peer fallback URLs")).toHaveTextContent("");
  });

  it("rejects peer fallback URLs with query delimiters", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ControlledPeerFallbackUrlList />);

    await user.type(screen.getByLabelText("Peer fallback base URL"), "http://127.0.0.1:2461?");

    expect(screen.getByRole("button", { name: "Add URL" })).toBeDisabled();
  });
});
