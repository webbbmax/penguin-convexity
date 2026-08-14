#!/usr/bin/env node
"use strict";

const { chromium } = require("playwright");

const BASE_URL = "http://127.0.0.1:8766/new-token-update.html";
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

async function runScenario(browser, response) {
  const page = await browser.newPage({ viewport: { width: 1180, height: 760 } });
  const requests = [];
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.route("**/api/c2.2/run", async (route) => {
    requests.push(JSON.parse(route.request().postData() || "{}"));
    await route.fulfill({
      status: response.httpStatus,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify(response.body),
    });
  });
  try {
    await page.goto(BASE_URL, { waitUntil: "networkidle" });
    const version = (await page.locator(".c19-admin-bottom strong").textContent())?.trim();
    if (version !== "当前版本 C2.3") throw new Error(`unexpected visible version: ${version}`);

    const button = page.locator('[data-c22-retry-source="project_website_identity"]');
    if (await button.count() !== 1) throw new Error("project website identity retry button is missing");
    await button.click();
    const card = button.locator("xpath=ancestor::*[contains(@class,'c22-source-card')][1]");
    const localFeedback = card.locator("[data-c22-source-feedback]");
    await localFeedback.waitFor({ state: "visible" });
    await page.waitForFunction(
      ({ expected }) => [...document.querySelectorAll("[data-c22-source-feedback]")]
        .some((node) => node.textContent.includes(expected)),
      { expected: response.expectedText },
    );
    const feedbackText = (await localFeedback.textContent())?.trim();
    if (!feedbackText?.includes(response.expectedText)) {
      throw new Error(`inline feedback mismatch: ${feedbackText}`);
    }
    if (requests.length !== 1) throw new Error(`expected one intercepted POST, received ${requests.length}`);
    const payload = requests[0];
    if (payload.jobCode !== "screening" || payload.sourceId !== "project_website_identity" || payload.trigger !== "manual") {
      throw new Error(`unexpected source retry payload: ${JSON.stringify(payload)}`);
    }
    const unexpectedConsoleErrors = response.httpStatus >= 400
      ? consoleErrors.filter((message) => !message.includes(String(response.httpStatus)))
      : consoleErrors;
    if (unexpectedConsoleErrors.length) {
      throw new Error(`browser console errors: ${JSON.stringify(unexpectedConsoleErrors)}`);
    }
    return { version, feedbackText, payload, productDataMutated: false };
  } finally {
    await page.close();
  }
}

async function main() {
  const browser = await chromium.launch({ executablePath: EDGE, headless: true });
  try {
    const accepted = await runScenario(browser, {
      httpStatus: 200,
      body: { status: "launched" },
      expectedText: "已受理",
    });
    const failed = await runScenario(browser, {
      httpStatus: 503,
      body: { error: "测试连接失败" },
      expectedText: "未能启动：测试连接失败",
    });
    console.log(JSON.stringify({ status: "passed", accepted, failed }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
