import { defineConfig } from "@playwright/test";

const baseURL = process.env.CODEX_LB_BROWSER_SMOKE_BASE_URL;
const outputDir = process.env.CODEX_LB_BROWSER_SMOKE_OUTPUT_DIR;

if (!baseURL || !outputDir) {
  throw new Error(
    "Run the dashboard browser smoke test through `make test-dashboard-browser-smoke`.",
  );
}

export default defineConfig({
  testDir: ".",
  testMatch: "dashboard.spec.ts",
  outputDir,
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "line",
  use: {
    baseURL,
    browserName: "chromium",
    headless: true,
    locale: "en-US",
    viewport: { width: 1440, height: 900 },
  },
});
