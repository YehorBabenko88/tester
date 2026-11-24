import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";

test("installed SCM terminal serves the attested release and ready worker", async ({ page, request }) => {
  test.skip(!process.env.TESTER_INSTALLED_URL, "Dedicated live-installed gate only");
  expect(process.env.TESTER_INSTALLED_SOURCE_SHA).toMatch(/^[0-9a-f]{40}$/);
  const failures = [];
  page.on("pageerror", (error) => failures.push(error.message));
  page.on("requestfailed", (request) => {
    if (request.failure()?.errorText !== "net::ERR_ABORTED")
      failures.push(`${request.url()}: ${request.failure()?.errorText}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`);
  });
  const health = await request.get("/api/health");
  expect([200, 503]).toContain(health.status());
  expect((await health.json()).data).toMatchObject({
    instance_id: process.env.TESTER_INSTALLED_INSTANCE,
    database: "ok", worker: "ok", storage: "ok",
  });
  const app = await request.get("/app.js");
  expect(app.status()).toBe(200);
  expect(createHash("sha256").update(await app.body()).digest("hex"))
    .toBe(process.env.TESTER_INSTALLED_APP_SHA256);
  await page.goto("/", { waitUntil: "networkidle" });
  await expect(page.locator("#terminal")).toBeAttached();
  await expect(page.locator("h1")).toBeVisible();
  await expect(page.locator("#screener-status")).toContainText("Loaded");
  await expect(page.locator("#screener-status")).not.toContainText("DATA_UNAVAILABLE");
  await page.getByRole("link", { name: "Terminal", exact: true }).click();
  await expect(page.locator("#terminal")).toBeVisible();
  const final = await request.get("/api/health");
  expect((await final.json()).data).toMatchObject({
    instance_id: process.env.TESTER_INSTALLED_INSTANCE,
    database: "ok", worker: "ok", storage: "ok",
  });
  expect(failures).toEqual([]);
});
