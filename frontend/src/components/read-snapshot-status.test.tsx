import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ReadSnapshotMetadata } from "@/schemas/read-snapshot";
import { ReadSnapshotStatus } from "./read-snapshot-status";

const SNAPSHOT: ReadSnapshotMetadata = {
  generatedAt: "2026-10-21T00:35:24Z",
  stale: false, refreshing: false, lastError: null, generation: 2,
  refreshIntervalSeconds: 300,
};

describe("ReadSnapshotStatus", () => {
  it("renders no helper copy for idle fresh data", () => {
    const { container } = render(<ReadSnapshotStatus snapshot={SNAPSHOT} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/Prepared|background refresh|Refreshing in the background/)).not.toBeInTheDocument();
  });
  it.each([{ stale: true }, { lastError: "collector failed" }])("retains a concise exceptional status: %j", (state) => {
    render(<ReadSnapshotStatus snapshot={{ ...SNAPSHOT, ...state }} />);
    expect(screen.getByRole("status")).toHaveTextContent(/Updates are delayed/);
    expect(screen.queryByText(/Prepared|last completed snapshot|collector failed/)).not.toBeInTheDocument();
  });
  it("shows a normal refresh without claiming that fresh data is delayed", () => {
    render(<ReadSnapshotStatus snapshot={{ ...SNAPSHOT, refreshing: true }} />);
    expect(screen.getByRole("status")).toHaveTextContent(/Loading/);
    expect(screen.queryByText(/Updates are delayed/)).not.toBeInTheDocument();
  });
  it("does not show preparation commentary before a first snapshot", () => {
    render(<ReadSnapshotStatus warming />);
    expect(screen.queryByText(/Preparing cached data|background/)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).not.toBeEmptyDOMElement();
  });
});
