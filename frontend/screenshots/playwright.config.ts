import { defineConfig } from "@playwright/test";

const screenshotPort = Number(process.env.SCREENSHOT_PORT ?? "4173");
const screenshotWebServerCommand =
  process.env.SCREENSHOT_WEBSERVER_COMMAND ??
  `bun run build && bun run preview --port ${screenshotPort}`;

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
    command: screenshotWebServerCommand,
    port: screenshotPort,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
