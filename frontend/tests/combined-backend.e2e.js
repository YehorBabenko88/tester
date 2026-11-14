import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("G-created exact job renders and receives D ACK in combined main app", async ({ page }) => {
  const fixture = JSON.parse(await readFile(process.env.TESTER_COMBINED_FIXTURE_FILE, "utf8"));
  const errors = [], wrongHttp = [], unexpectedCatalog = [];
  const networkEvidence = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("response", response => {
    const url = new URL(response.url());
    if (!url.pathname.startsWith("/api/")) return;
    networkEvidence.push({path: url.pathname, status: response.status()});
    if ([404, 422, 503].includes(response.status()))
      wrongHttp.push({path: url.pathname, status: response.status()});
  });
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/market-catalog/") || path === "/api/analytics")
      unexpectedCatalog.push(path);
  });
  await page.goto("/");
  await expect(page.locator("body")).toContainText(/HISTORICAL LAB/i);
  await expect(page.locator("#screener-status"))
    .toContainText("market_catalog_not_configured");
  const caps = await page.request.get("/api/capabilities");
  expect(caps.status()).toBe(200);
  const capabilityData = (await caps.json()).data;
  expect(capabilityData.features.market_catalog.enabled).toBe(false);
  expect(capabilityData.features.sources.enabled).toBe(false);
  expect(capabilityData.observed_page_revision.history_complete).toBe(false);
  expect(capabilityData.source_contracts.public_executions.contract_version)
    .toBe("tester-public-execution/1");
  expect(unexpectedCatalog).toEqual([]);
  expect(wrongHttp).toEqual([]);
  await page.goto("/research.html");
  const withoutToken = await page.request.post("/api/control/research", {
    data: fixture.command,
  });
  expect(withoutToken.status()).toBe(401);
  await page.locator("#research-command").fill(JSON.stringify(fixture.command));
  await page.getByRole("button", { name: "Read authorized research chart" }).click();
  await expect(page.locator("#research-status")).toContainText(/DATA_UNAVAILABLE.*(401|403)/);
  await expect(page.locator("#research-canvas")).not.toHaveAttribute("data-render-ack", "accepted");
  const submits = [];
  const results = [];
  page.on("response", async r => {
    const path = new URL(r.url()).pathname;
    if (r.ok() && path === "/api/control/research" && r.request().method() === "POST") submits.push(await r.json());
    if (r.ok() && path === `/api/control/research/${fixture.job_id}` && r.request().method() === "GET") results.push(await r.json());
  });
  await page.locator("#capability").fill(fixture.token);
  await page.getByRole("button", { name: "Read authorized research chart" }).click();
  const canvas = page.locator("#research-canvas");
  await expect(canvas).toHaveAttribute("data-render-ack", "accepted");
  expect(submits).toHaveLength(1);
  expect(submits[0].job_id).toBe(fixture.job_id);
  const result = results.find(r => r.state === "complete");
  expect(result.artifact_digest).toBe(fixture.command.expected_artifact_digest);
  // A correctly authenticated actor cannot acknowledge a different chart
  // digest. This negative request must not create/replace the stored ACK.
  const falseAck = await page.request.post(
    `/api/control/research/${fixture.job_id}/ack`,
    {
      headers: { Authorization: `Bearer ${fixture.token}` },
      data: {
        artifact_digest: "0".repeat(64),
        rendered_fill_ids: result.artifact.markers.map(m => m.fill_id),
      },
    },
  );
  expect(falseAck.status()).toBe(409);
  const missingTokenResult = await page.request.get(
    `/api/control/research/${fixture.job_id}`,
  );
  expect(missingTokenResult.status()).toBe(401);
  const ids = result.artifact.markers.map(m => m.fill_id).sort();
  expect(JSON.parse(await canvas.getAttribute("data-rendered-fill-ids")).sort()).toEqual(ids);
  await expect(page.locator("#research-fills tr")).toHaveCount(ids.length);
  await expect(page.locator("#research-status")).toContainText("Real market coverage NOT PROVEN");
  await expect(page.locator("#capability")).toHaveValue("");
  expect(await canvas.evaluate(c => {
    const data = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    let count = 0;
    for (let i = 3; i < data.length; i += 4) if (data[i]) count++;
    return count;
  })).toBeGreaterThan(100);
  expect(errors).toEqual([]);
  expect(wrongHttp).toEqual([]);
  expect(unexpectedCatalog).toEqual([]);
  expect(networkEvidence.some(r => r.path === "/api/capabilities" && r.status === 200)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("combined-synthetic.png"), fullPage: true });
});
