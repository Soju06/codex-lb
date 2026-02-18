import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "capture.spec.ts",
  timeout: 60_000,
  workers: 1,
  use: {
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    launchOptions: {
      args: ["--use-gl=angle", "--use-angle=swiftshader"],
    },
  },
  webServer: {
    command: "bun run build && bun run preview --port 4173",
    port: 4173,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
