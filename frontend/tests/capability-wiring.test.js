import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { classifyPanelCapability } from "../capabilities.js";

const app = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const intervals = ["1","3","5","15","30","60","120","240","360","720","D","W","M"];

test("all 13 Bybit intervals appear exactly once in the real UI", () => {
  const options = [...html.matchAll(/<option value="([^"]+)"[^>]*>[^<]+<\/option>/g)]
    .map(m => m[1]);
  for (const interval of intervals) assert.equal(options.filter(v => v === interval).length, 1, interval);
});
test("real app gates each panel before requesting resources", () => {
  assert.match(app, /classifyPanelCapability\(kind, capabilities\[kind\]\)/);
  assert.match(app, /if \(!capability\.requestAllowed\)/);
  assert.match(app, /const path = capability\.resource/);
  assert.doesNotMatch(app, /panelEndpoint\(kind\)/);
});
test("mark/index are declared and require source-qualified capability", () => {
  for (const panel of ["mark", "index"]) {
    assert.match(readFileSync(new URL("../model.js", import.meta.url), "utf8"), new RegExp('\\["' + panel + '",'));
    assert.equal(classifyPanelCapability(panel, null).requestAllowed, false);
    assert.equal(classifyPanelCapability(panel, {
      panel, status: "AVAILABLE", reason: "source_qualified",
      resource: "/api/candles", revision: "snapshot:1", source_ref: "bybit:mark:1"
    }).requestAllowed, true);
  }
});
test("no speculative analytics or footprint requests on missing discovery", () => {
  for (const panel of ["footprint","delta","cvd","open_interest","funding","mark","index"]) {
    assert.equal(classifyPanelCapability(panel, {}).requestAllowed, false);
  }
});

test("mark and index request distinct official candle kinds", () => {
  assert.match(app, /kind === "mark" \? \{ candle_kind: "official_mark" \}/);
  assert.match(app, /kind === "index" \? \{ candle_kind: "official_index" \}/);
  const series = readFileSync(new URL("../series.js", import.meta.url), "utf8");
  assert.match(series, /\["mark", "index"\]\.includes\(kind\)/);
  assert.match(series, /\{ value: r\.close \}/);
});
