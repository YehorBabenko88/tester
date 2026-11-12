import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
for (const filename of [
  "playwright.pc4.config.js",
  "playwright.pc4-strategy.config.js",
]) {
  const source = readFileSync(
    new URL(`../${filename}`, import.meta.url),
    "utf8",
  )
    .replace(
      'import { defineConfig } from "@playwright/test";',
      "const defineConfig = value => value;",
    )
    .replace(
      "export default defineConfig(",
      "globalThis.result = defineConfig(",
    );
  const load = (value) => {
    const env = {
      TESTER_PC4_OPT_IN: "pc4-owned",
      TESTER_PC4_CASE_FILE: "fixture-not-read.json",
      TESTER_PC4_STRATEGY_CASE_FILE: "private-fixture-not-read.json",
      TESTER_PC4_SOURCE_SHA: "a".repeat(40),
      TESTER_PC4_HARNESS_SHA: "b".repeat(40),
    };
    if (value !== undefined) env.TESTER_PC4_DEVICE_SCALE_FACTOR = value;
    const context = { process: { env } };
    runInNewContext(source, context, { filename });
    return context.result;
  };
  test(`${filename}: explicit 1x/2x and default are retained`, () => {
    assert.equal(load(undefined).use.deviceScaleFactor, 1);
    assert.equal(load("1").use.deviceScaleFactor, 1);
    assert.equal(load("2").use.deviceScaleFactor, 2);
  });
  test(`${filename}: malformed scale fails before browser creation`, () => {
    for (const value of ["", "0", "3", "1.5", "2x", " 2", "NaN"])
      assert.throws(() => load(value));
  });
}
