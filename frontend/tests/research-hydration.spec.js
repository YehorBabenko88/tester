import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";

test("research submit waits until its event handler is attached", async ({
  page,
}) => {
  let release;
  const moduleReady = new Promise((resolve) => {
    release = resolve;
  });
  await page.route("http://terminal.test/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/research-app.js") await moduleReady;
    const contentType = pathname.endsWith(".html")
      ? "text/html"
      : pathname.endsWith(".css")
        ? "text/css"
        : "text/javascript";
    return route.fulfill({
      contentType,
      body: await readFile(path.join(process.cwd(), pathname.slice(1))),
    });
  });
  try {
    await page.goto("/research.html", { waitUntil: "commit" });
    await expect(page.locator("#research-submit")).toBeVisible();
    await expect(page.locator("#research-submit")).toBeDisabled();
  } finally {
    release();
  }
  await expect(page.locator("#research-submit")).toBeEnabled();
  await page.locator("#research-command").fill("not-json");
  await page.locator("#research-submit").click();
  await expect(page.locator("#research-status")).toContainText(
    "Invalid approved command",
  );
});
