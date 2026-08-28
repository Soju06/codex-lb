import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ApiList } from "@/features/apis/components/api-list";
import { createApiKey } from "@/test/mocks/factories";

describe("ApiList", () => {
  it("shows first-run empty copy when no API keys exist", () => {
    render(
      <ApiList apiKeys={[]} selectedKeyId={null} onSelect={() => {}} onOpenCreate={() => {}} />,
    );

    expect(screen.getByText("No API keys yet")).toBeInTheDocument();
    expect(screen.getByText("Create an API key to authenticate clients.")).toBeInTheDocument();
    expect(screen.queryByText("Adjust filters")).not.toBeInTheDocument();
  });

  it("shows filter-empty copy when keys exist but none match", async () => {
    const user = userEvent.setup();

    render(
      <ApiList
        apiKeys={[createApiKey({ name: "Fleet key", keyPrefix: "sk-fleet" })]}
        selectedKeyId={null}
        onSelect={() => {}}
        onOpenCreate={() => {}}
      />,
    );

    await user.type(screen.getByPlaceholderText("Search API keys..."), "not-found");

    expect(screen.getByText("No matching API keys")).toBeInTheDocument();
    expect(screen.getByText("Adjust filters")).toBeInTheDocument();
  });
});
