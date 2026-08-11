#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
const OUTPUT = path.join(ROOT, "reports", "c2.1-final-acceptance", "screenshots");
const BASE = "http://127.0.0.1:8766/";
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const STATE_LABEL = {
  convexity_clue: "凸性线索",
  active_project: "活跃项目",
  early_observation: "新发观察",
  continuous_observation: "持续观察",
  data_limited: "数据受限",
};

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function fixtureSnapshot(original, items) {
  const result = clone(original);
  result.items = items;
  result.statusCounts = Object.fromEntries(Object.keys(STATE_LABEL).map((key) => [key, 0]));
  for (const item of items) result.statusCounts[item.displayState.code] += 1;
  result.coverageSummary.frontVisibleCount = items.length;
  result.coverageSummary.hardGatePassedCount = items.length;
  result.coverageSummary.added24hCount = items.filter((item) => item.ageDays <= 1).length;
  result.sourceImpactSummary = items.some((item) => item.displayState.code === "data_limited")
    ? clone(original.sourceImpactSummary)
    : { status: "healthy", affectedProjectCount: 0, affectedChains: [], affectedFields: [], lastSuccessfulAt: original.sourceCutoffAt, reasonCode: "", plainReason: "当前关键来源可用。", expectedRecoveryAt: null };
  return result;
}

async function renderFixture(page, url, snapshot) {
  await page.goto(BASE + url, { waitUntil: "networkidle" });
  await page.evaluate(async (payload) => {
    window.PENGUIN_CONVEXITY_C21 = payload;
    const main = document.querySelector("main");
    if (main) main.innerHTML = "";
    const source = await (await fetch("c2-1-front.js?v=acceptance", { cache: "no-store" })).text();
    window.eval(source);
  }, snapshot);
  await page.waitForTimeout(80);
}

async function capture(page, name, kind, notes) {
  const target = path.join(OUTPUT, name);
  await page.screenshot({ path: target, fullPage: false });
  const metrics = await page.evaluate(() => ({
    viewport: [window.innerWidth, window.innerHeight],
    scrollWidth: document.documentElement.scrollWidth,
    hasHorizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
    title: document.querySelector("main h1")?.textContent?.trim() || "",
  }));
  if (metrics.hasHorizontalOverflow) throw new Error(`${name} has horizontal overflow`);
  return { file: name, kind, notes, ...metrics };
}

async function main() {
  fs.mkdirSync(OUTPUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: EDGE, headless: true });
  const context = await browser.newContext({ viewport: { width: 1180, height: 760 } });
  const page = await context.newPage();
  const results = [];
  try {
    await page.goto(BASE + "candidate-pool.html", { waitUntil: "networkidle" });
    const original = await page.evaluate(() => window.PENGUIN_CONVEXITY_C21);
    if (!original || !original.items?.length) throw new Error("C2.1 real front snapshot is unavailable");
    const githubItem = original.items.find((item) => item.productEvidence?.github?.status === "qualifying") || original.items[0];
    const limitedItem = original.items.find((item) => item.displayState.code === "data_limited") || original.items[0];
    const baseItem = original.items.find((item) => item.displayState.code !== "data_limited") || original.items[0];

    results.push(await capture(page, "01-home-real-projects-1180x760.png", "real", "首页有合格项目"));

    const zero = fixtureSnapshot(original, []);
    zero.blockerCounts = { observed_buy: 8, observed_sell: 7, product_evidence_present: 4 };
    await renderFixture(page, "candidate-pool.html", zero);
    results.push(await capture(page, "02-home-zero-fixture-1180x760.png", "fixture", "浏览器内零结果夹具；未写入产品数据"));

    const mixedItems = Object.keys(STATE_LABEL).map((code, index) => {
      const item = clone(original.items[index % original.items.length]);
      item.projectId = `acceptance-mixed-${index}`;
      item.detailUrl = `project-detail.html?id=${item.projectId}`;
      item.canonicalName = `验收样本 ${index + 1}`;
      item.displayState = { ...item.displayState, code, label: STATE_LABEL[code], reason: `${STATE_LABEL[code]}的冻结排版验收。` };
      item.sourceImpact = code === "data_limited" ? clone(limitedItem.sourceImpact) : { ...item.sourceImpact, status: "healthy", plainReason: "当前关键来源可用。" };
      return item;
    });
    const mixed = fixtureSnapshot(original, mixedItems);
    await renderFixture(page, "candidate-pool.html?view=all", mixed);
    results.push(await capture(page, "03-all-five-states-fixture-1180x760.png", "fixture", "浏览器内五状态混合夹具"));

    await page.goto(BASE + `candidate-pool.html?view=all&evidenceType=github&q=${encodeURIComponent(githubItem.symbol)}`, { waitUntil: "networkidle" });
    results.push(await capture(page, "04-all-github-only-real-1180x760.png", "real", "真实仅代码证据筛选"));

    await page.goto(BASE + limitedItem.detailUrl, { waitUntil: "networkidle" });
    results.push(await capture(page, "05-data-limited-detail-real-1180x760.png", "real", "真实数据受限详情"));

    await page.goto(BASE + "candidate-pool.html?view=all&state=convexity_clue&evidenceType=deployed_product", { waitUntil: "networkidle" });
    results.push(await capture(page, "06-filter-empty-real-1180x760.png", "real", "真实多条件筛选零结果"));

    const many = Array.from({ length: 25 }, (_, index) => {
      const item = clone(original.items[index % original.items.length]);
      item.canonicalName = `${item.canonicalName} · 页码样本${index + 1}`;
      return item;
    });
    const paged = fixtureSnapshot(original, many);
    const pagedRoute = (route) => route.fulfill({
      contentType: "application/javascript; charset=utf-8",
      body: `window.PENGUIN_CONVEXITY_C21 = ${JSON.stringify(paged)};`,
    });
    await page.route("**/c2-1-front-snapshot.js*", pagedRoute);
    await page.goto(BASE + "candidate-pool.html?view=all&page=2", { waitUntil: "networkidle" });
    const pageTwoLink = page.locator("[data-detail-link]").first();
    await pageTwoLink.click();
    await page.goBack({ waitUntil: "networkidle" });
    await page.waitForTimeout(100);
    const returnedPage = await page.evaluate(() => ({
      url: location.pathname.split("/").pop() + location.search,
      currentPage: document.querySelector('[data-page][aria-current="page"]')?.textContent?.trim(),
    }));
    if (!returnedPage.url.includes("page=2") || returnedPage.currentPage !== "2") {
      throw new Error(`page 2 return state was not restored: ${JSON.stringify(returnedPage)}`);
    }
    results.push(await capture(page, "07-page2-return-fixture-1180x760.png", "fixture", "浏览器内25项分页夹具；已执行进入详情并返回"));
    await page.unroute("**/c2-1-front-snapshot.js*", pagedRoute);

    const early = clone(baseItem);
    early.projectId = "acceptance-early";
    early.detailUrl = "project-detail.html?id=acceptance-early";
    early.ageDays = 1;
    early.ageBand = "age_0_30";
    early.displayState = { ...early.displayState, code: "early_observation", label: "新发观察", reason: "真实历史不足14天且尚未形成强/弱线索；不是等待期或年龄扣分。" };
    early.observationHistory = { ...early.observationHistory, ageDays: 1, expectedHistoryDays: 2, validHistoryDays: 2, gapDays: 0 };
    const earlySnapshot = fixtureSnapshot(original, [early]);
    await renderFixture(page, early.detailUrl, earlySnapshot);
    results.push(await capture(page, "08-early-day1-detail-fixture-1180x760.png", "fixture", "浏览器内第1天新发观察边界夹具"));

    const clue = clone(baseItem);
    clue.projectId = "acceptance-clue";
    clue.detailUrl = "project-detail.html?id=acceptance-clue";
    clue.ageDays = Math.max(14, Math.min(90, clue.ageDays));
    clue.displayState = { ...clue.displayState, code: "convexity_clue", label: "凸性线索", reason: "已形成至少两条可复算强证据路径，其中包含交易与流动性；不是上涨预测。" };
    clue.evidencePaths[0].status = "formed";
    clue.evidencePaths[1].status = "formed";
    const clueSnapshot = fixtureSnapshot(original, [clue]);
    await renderFixture(page, clue.detailUrl, clueSnapshot);
    results.push(await capture(page, "09-clue-day14-90-detail-fixture-1180x760.png", "fixture", "浏览器内14—90天凸性线索排版夹具"));

    await page.goto(BASE + githubItem.detailUrl, { waitUntil: "networkidle" });
    results.push(await capture(page, "10-github-boundary-real-1180x760.png", "real", "真实GitHub-only边界说明"));

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(BASE + "candidate-pool.html", { waitUntil: "networkidle" });
    results.push(await capture(page, "11-home-real-projects-1440x900.png", "real", "1440×900桌面窗口"));

    await page.setViewportSize({ width: 1180, height: 760 });
    await page.goto(BASE + "change-explanations.html", { waitUntil: "networkidle" });
    results.push(await capture(page, "12-important-changes-real-1180x760.png", "real", "同项目同轮变化分组"));

    const manifest = {
      schemaVersion: "c2.1-screenshot-manifest-v1",
      generatedAt: new Date().toISOString(),
      productDataMutated: false,
      fixtureBoundary: "fixture仅存在于浏览器内存，用于冻结状态的UI边界验收；不写数据库、不写正式快照。",
      screenshots: results,
    };
    fs.writeFileSync(path.join(OUTPUT, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n", "utf8");
    console.log(JSON.stringify({ count: results.length, output: OUTPUT }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
