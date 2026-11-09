import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("G-created exact job renders and receives D ACK in combined main app", async ({ page }) => {
  const fixture = JSON.parse(await readFile(process.env.TESTER_COMBINED_FIXTURE_FILE, "utf8"));
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto("/");
  await expect(page.locator("body")).toContainText(/Tester/i);
  await page.goto("/research.html");
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
  await page.screenshot({ path: test.info().outputPath("combined-synthetic.png"), fullPage: true });
});
