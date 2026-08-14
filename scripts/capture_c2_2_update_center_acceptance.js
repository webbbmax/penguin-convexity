#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
const OUTPUT = path.join(ROOT, "reports", "c2.2-update-center-fix");
const BASE = "http://127.0.0.1:8766/";
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

async function inspectPage(page, route, viewport, expected) {
  await page.setViewportSize(viewport);
  const consoleErrors = [];
  const failedRequests = [];
  const posts = [];
  const onConsole = (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  };
  const onFailed = (request) => failedRequests.push(`${request.method()} ${request.url()}`);
  const onRequest = (request) => {
    if (request.method() === "POST") posts.push(request.url());
  };
  page.on("console", onConsole);
  page.on("requestfailed", onFailed);
  page.on("request", onRequest);
  try {
    await page.goto(BASE + route, { waitUntil: "networkidle" });
    await page.waitForTimeout(700);
    const metrics = await page.evaluate(() => ({
      viewport: [window.innerWidth, window.innerHeight],
      scrollWidth: document.documentElement.scrollWidth,
      manualButton: document.querySelector("#c22RunNow")?.textContent?.trim(),
      liveTitle: document.querySelector("#c22LiveTitle")?.textContent?.trim(),
      sourceOwners: [...document.querySelectorAll(".c22-source-card>div:first-child>span")].map((node) => node.textContent.trim()),
      legacyOpen: Boolean(document.querySelector(".c22-legacy-details")?.open),
      workbenchTitle: document.querySelector("#c22WorkbenchTitle")?.textContent?.trim() || "",
      workbenchTask: document.querySelector("#c22WorkbenchTask")?.textContent?.trim() || "",
      containsSimulatedFailure: document.body.textContent.includes("模拟外部信源超时"),
      productionTitle: document.querySelector("#c22ProductionTitle")?.textContent?.trim() || "",
      productionImported: document.querySelector("#c22ProductionImported")?.textContent?.trim() || "",
      productionBoundary: document.querySelector("#c22ProductionBoundary")?.textContent?.trim() || "",
      productionRunDisabled: Boolean(document.querySelector("#c22ProductionRun")?.disabled),
      dailyFunnelTitle: document.querySelector("#c22DailyFunnel h3")?.textContent?.trim() || "",
    }));
    if (metrics.scrollWidth > viewport.width) throw new Error(`${route} has horizontal overflow`);
    if (metrics.manualButton !== expected.manualButton) throw new Error(`${route} manual control is missing`);
    if (!metrics.liveTitle) throw new Error(`${route} live progress is missing`);
    if (metrics.sourceOwners.some((owner) => !expected.owners.includes(owner))) {
      throw new Error(`${route} contains a source owned by another job: ${metrics.sourceOwners.join(", ")}`);
    }
    if (typeof expected.legacyOpen === "boolean" && metrics.legacyOpen !== expected.legacyOpen) {
      throw new Error(`unexpected initial legacy panel state: ${metrics.legacyOpen}`);
    }
    if (expected.liveNotFailed && /失败|中断/.test(metrics.liveTitle)) {
      throw new Error(`legacy failure leaked into current job status: ${metrics.liveTitle}`);
    }
    if (expected.workbenchTitle && metrics.workbenchTitle !== expected.workbenchTitle) {
      throw new Error(`unexpected workbench status: ${metrics.workbenchTitle}`);
    }
    if (expected.workbenchTask && metrics.workbenchTask !== expected.workbenchTask) {
      throw new Error(`unexpected workbench task: ${metrics.workbenchTask}`);
    }
    if (metrics.containsSimulatedFailure) throw new Error("test-only failure leaked into the product page");
    if (route === "new-token-update.html") {
      if (!metrics.productionTitle.includes("历史候选基础扫描")) throw new Error("candidate production area is missing");
      if (metrics.dailyFunnelTitle !== "本页日常候选去向") throw new Error("daily high-priority funnel is missing");
      if (!metrics.productionImported.includes("459")) throw new Error(`candidate total is not visible: ${metrics.productionImported}`);
      if (!metrics.productionBoundary.includes("尚未启动") || !metrics.productionBoundary.includes("不会由页面或自动任务越权启动")) {
        throw new Error(`formal scan boundary is unclear: ${metrics.productionBoundary}`);
      }
      if (!metrics.productionRunDisabled) throw new Error("unauthorized formal history button is enabled");
      const productionName = `candidate-production-${viewport.width}x${viewport.height}.png`;
      await page.locator("#c22CandidateProduction").screenshot({ path: path.join(OUTPUT, productionName) });
      metrics.productionScreenshot = productionName;
      await page.evaluate(() => window.scrollTo(0, 0));
    }
    if (route === "update-center.html") {
      const legacyName = `update-center-history-${viewport.width}x${viewport.height}.png`;
      await page.locator("#c22WorkbenchBridge").screenshot({ path: path.join(OUTPUT, legacyName) });
      metrics.legacyScreenshot = legacyName;
      await page.locator(".c22-legacy-details").evaluate((node) => { node.open = false; });
      await page.locator("#c22OpenLegacy").click();
      if (!await page.locator(".c22-legacy-details").evaluate((node) => node.open)) {
        throw new Error("workbench detail button did not open the inherited panel");
      }
      await page.evaluate(() => window.scrollTo(0, 0));
    }
    const name = `${route.replace(".html", "")}-${viewport.width}x${viewport.height}.png`;
    await page.screenshot({ path: path.join(OUTPUT, name), fullPage: false });
    if (posts.length) throw new Error(`acceptance unexpectedly sent POST: ${posts.join(", ")}`);
    if (consoleErrors.length || failedRequests.length) {
      throw new Error(`${route} browser errors: ${JSON.stringify({ consoleErrors, failedRequests })}`);
    }
    return { route, screenshot: name, ...metrics, consoleErrors, failedRequests, posts };
  } finally {
    page.off("console", onConsole);
    page.off("requestfailed", onFailed);
    page.off("request", onRequest);
  }
}

async function main() {
  fs.mkdirSync(OUTPUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: EDGE, headless: true });
  const page = await browser.newPage();
  const results = [];
  try {
    for (const viewport of [{ width: 1180, height: 760 }, { width: 1440, height: 900 }]) {
      results.push(await inspectPage(page, "new-token-update.html", viewport, {
        manualButton: "立即手动更新新币筛选",
        owners: ["90天新币筛选", "共享上游"],
      }));
      results.push(await inspectPage(page, "update-center.html", viewport, {
        manualButton: "立即手动更新凸性跟踪",
        owners: ["凸性跟踪", "共享上游"],
        legacyOpen: false,
        liveNotFailed: true,
        workbenchTitle: "历史任务保留失败记录",
        workbenchTask: "机器状态与结论发布",
      }));
    }
    const manifest = {
      schemaVersion: "c2.2-update-center-repair-acceptance-v1",
      generatedAt: new Date().toISOString(),
      productDataMutated: false,
      checks: results,
    };
    fs.writeFileSync(path.join(OUTPUT, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n", "utf8");
    console.log(JSON.stringify({ checks: results.length, output: OUTPUT }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
