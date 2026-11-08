import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
test("real authorized durable research result draws and acknowledges exact fills", async ({
  page,
}) => {
  const token = process.env.TESTER_RESEARCH_TOKEN;
  const command = JSON.parse(
    await readFile(process.env.TESTER_RESEARCH_COMMAND_FILE, "utf8"),
  );
  expect(token, "Root-provided disposable capability required").toBeTruthy();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/research.html");
  await page
    .locator("#research-command")
    .fill(JSON.stringify({ ...command, request_id: randomUUID() }));
  await page
    .getByRole("button", { name: "Read authorized research chart" })
    .click();
  await expect(page.locator("#research-status")).toContainText(
    /DATA_UNAVAILABLE.*(401|403)/,
  );
  await expect(page.locator("#research-canvas")).not.toHaveAttribute(
    "data-render-ack",
    "accepted",
  );
  const results = [];
  page.on("response", async (r) => {
    if (
      /\/api\/control\/research\/[^/]+$/.test(new URL(r.url()).pathname) &&
      r.request().method() === "GET" &&
      r.ok()
    )
      results.push(await r.json());
  });
  await page.locator("#capability").fill(token);
  await page
    .locator("#research-command")
    .fill(JSON.stringify({ ...command, request_id: randomUUID() }));
  await page
    .getByRole("button", { name: "Read authorized research chart" })
    .click();
  const canvas = page.locator("#research-canvas");
  await expect(canvas).toHaveAttribute("data-render-ack", "accepted");
  await expect(page.locator("#research-status")).toContainText(
    "Real market coverage NOT PROVEN",
  );
  await expect(page.locator("#capability")).toHaveValue("");
  const result = results.find((r) => r.state === "complete");
  expect(result).toBeTruthy();
  const artifact = result.artifact;
  expect(artifact.markers.some((m) => m.role === "entry")).toBe(true);
  expect(artifact.markers.some((m) => m.role === "exit")).toBe(true);
  expect(
    artifact.markers.every((m) => m.known_at_ms <= artifact.cutoff_ms),
  ).toBe(true);
  const ids = artifact.markers.map((m) => m.fill_id).sort();
  expect(
    JSON.parse(await canvas.getAttribute("data-rendered-fill-ids")).sort(),
  ).toEqual(ids);
  await expect(page.locator("#research-fills tr")).toHaveCount(ids.length);
  for (const m of artifact.markers)
    await expect(
      page.locator("#research-fills tr").filter({ hasText: m.fill_id }),
    ).toContainText(m.price);
  const pixels = await canvas.evaluate((c) => {
    const image = c.getContext("2d").getImageData(0, 0, c.width, c.height);
    let painted = 0;
    for (let i = 3; i < image.data.length; i += 4) if (image.data[i]) painted++;
    return { painted, width: c.width, cssWidth: c.clientWidth };
  });
  expect(pixels.painted).toBeGreaterThan(100);
  expect(pixels.width).toBe(pixels.cssWidth * 2);
  const full = JSON.parse(await canvas.getAttribute("data-view-range"));
  await canvas.hover();
  await page.mouse.wheel(0, -300);
  await expect
    .poll(
      async () =>
        JSON.parse(await canvas.getAttribute("data-view-range"))[1] -
        JSON.parse(await canvas.getAttribute("data-view-range"))[0],
    )
    .toBeLessThan(full[1] - full[0]);
  await canvas.focus();
  await page.keyboard.press("ArrowRight");
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.5);
  await page.mouse.up();
  const visibleRange = JSON.parse(await canvas.getAttribute("data-view-range"));
  const expected = artifact.markers
    .filter(
      (m) =>
        m.timestamp_ms >= visibleRange[0] &&
        m.timestamp_ms < visibleRange[1] &&
        m.known_at_ms <= artifact.cutoff_ms,
    )
    .map((m) => m.fill_id)
    .sort();
  expect(
    JSON.parse(await canvas.getAttribute("data-rendered-fill-ids")).sort(),
  ).toEqual(expected);
  const geometry = JSON.parse(
    await canvas.getAttribute("data-rendered-geometry"),
  );
  for (const marker of geometry.markers) {
    expect(marker.x).toBeGreaterThanOrEqual(geometry.bounds.left);
    expect(marker.x).toBeLessThanOrEqual(geometry.bounds.right);
    expect(marker.y).toBeGreaterThanOrEqual(geometry.bounds.top);
    expect(marker.y).toBeLessThanOrEqual(geometry.bounds.bottom);
    expect(marker.window[0]).toBeGreaterThanOrEqual(geometry.bounds.left);
    expect(marker.window[1]).toBeLessThanOrEqual(geometry.bounds.right);
  }
  await page.getByRole("button", { name: "Full range", exact: true }).click();
  expect(
    JSON.parse(await canvas.getAttribute("data-rendered-fill-ids")).sort(),
  ).toEqual(ids);
  await page.screenshot({
    path: test.info().outputPath("real-research-chart.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
