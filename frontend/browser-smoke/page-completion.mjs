// Real built pages and backend: no route mocks, artificial delays or API-only clocks.
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { performance } from "node:perf_hooks";

const [baseURL, output] = process.argv.slice(2);
if (!baseURL?.startsWith("http://127.0.0.1:") || !output) {
  throw new Error("Usage: bun browser-smoke/page-completion.mjs LOOPBACK_URL OUTPUT_DIR");
}
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
    : {}),
});
const results = [];
try {
  for (const [name, path, required] of [
    ["dashboard", "/dashboard", ["/api/dashboard/overview", "/api/dashboard/projections", "/api/request-logs", "/api/request-logs/options"]],
    ["reports", "/reports", ["/api/reports", "/api/reports/options", "/api/reports/thread-identity"]],
  ]) {
    const context = await browser.newContext({ locale: "en-US", timezoneId: "UTC", viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const pending = new Set();
    const completed = new Set();
    const failures = [];
    const business = {};
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/")) pending.add(request);
    });
    page.on("requestfailed", (request) => {
      pending.delete(request);
      failures.push(`${new URL(request.url()).pathname}: ${request.failure()?.errorText}`);
    });
    page.on("response", async (response) => {
      const pathname = new URL(response.url()).pathname;
      if (!pathname.startsWith("/api/")) return;
      try {
        if (!response.ok()) failures.push(`${pathname}: HTTP ${response.status()}`);
        const payload = await response.json();
        if (name === "dashboard" && pathname === "/api/dashboard/overview") business.summary = payload.summary;
        if (name === "reports" && pathname === "/api/reports") business.summary = payload.summary;
        completed.add(pathname);
      } catch (error) {
        failures.push(`${pathname}: ${String(error)}`);
      } finally {
        pending.delete(response.request());
      }
    });
    const start = performance.now();
    await page.goto(`${baseURL}${path}`, { waitUntil: "domcontentloaded" });
    const deadline = start + 120_000;
    while (required.slice(0, 2).some((route) => !completed.has(route))) {
      if (failures.length || performance.now() > deadline) throw new Error(JSON.stringify({ name, failures }));
      await page.waitForTimeout(20);
    }
    // Visit below-fold lazy panels too; a first-viewport result is not full completion.
    for (let y = 0; y < await page.locator("body").evaluate((body) => body.scrollHeight); y += 600) {
      await page.evaluate((position) => window.scrollTo(0, position), y);
      await page.waitForTimeout(50);
    }
    await page.evaluate(() => window.scrollTo(0, 0));
    while (required.some((route) => !completed.has(route)) || pending.size) {
      if (failures.length || performance.now() > deadline) {
        throw new Error(JSON.stringify({ name, failures, completed: [...completed], pending: pending.size }));
      }
      await page.waitForTimeout(20);
    }
    // Include React rendering and chart animation, not just response completion.
    await page.getByText("Loading...", { exact: true }).waitFor({ state: "hidden" });
    let previous = "";
    let stable = 0;
    while (stable < 3) {
      const current = await page.locator("main").evaluate((main) => main.innerHTML);
      stable = current === previous ? stable + 1 : 0;
      previous = current;
      if (performance.now() > deadline) throw new Error(`${name}: rendered DOM did not settle`);
      await page.waitForTimeout(100);
    }
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    if (failures.length) throw new Error(JSON.stringify(failures));
    if (!business.summary || !Object.keys(business.summary).length) {
      throw new Error(`${name}: missing populated summary`);
    }
    if (!(await page.locator("main table").count())) {
      throw new Error(`${name}: expected fully rendered data table`);
    }
    if (await page.getByRole("dialog").count()) throw new Error(`${name}: unexpected dialog covers the page`);
    const completionMs = performance.now() - start;
    await page.screenshot({ path: `${output}/${name}.png`, fullPage: true });
    results.push({ name, completionMs, completed: [...completed].sort(), business });
    await context.close();
  }
  writeFileSync(`${output}/completion.json`, JSON.stringify(results, null, 2) + "\n");
  console.log(JSON.stringify(results.map(({ business: _business, ...result }) => result)));
} finally {
  await browser.close();
}
