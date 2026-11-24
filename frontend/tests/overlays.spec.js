import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";
test("trader workspace states source and permission gates without making requests", async ({
  page,
}) => {
  const requests = [];
  page.on("request", (r) => requests.push(r.url()));
  await page.route("http://terminal.test/**", async (route) => {
    const u = new URL(route.request().url());
    if (u.pathname === "/")
      return route.fulfill({
        contentType: "text/html",
        body: '<main id="root"></main><script type="module">import {mountStrategyWorkspace} from "/strategy.js"; mountStrategyWorkspace(document.querySelector("main"));</script>',
      });
    return route.fulfill({
      contentType: "text/javascript",
      body: await readFile(path.join(process.cwd(), u.pathname.slice(1))),
    });
  });
  await page.goto("/");
  const region = page.getByRole("region", {
    name: "Trader disclosures and hypotheses",
  });
  await expect(region).toContainText("DATA_UNAVAILABLE");
  await expect(region).toContainText("NOT_INTEGRATED");
  await expect(region).toContainText("permission UNKNOWN");
  await expect(region).toContainText("DISCLOSED");
  await expect(region).toContainText("INFERRED");
  expect(requests.some((u) => u.includes("/api/"))).toBe(false);
});
