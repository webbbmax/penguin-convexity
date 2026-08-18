"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

function option(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? path.resolve(process.argv[index + 1]) : fallback;
}

const projectRoot = path.resolve(__dirname, "..");
const frontScriptPath = option("--front-script", path.join(projectRoot, "app", "c2-4-front.js"));
const snapshotPath = option("--snapshot", path.join(projectRoot, "app", "c2-4-front-snapshot.js"));

function loadSnapshot(snapshotFile) {
  const context = { window: {} };
  vm.runInNewContext(fs.readFileSync(snapshotFile, "utf8"), context, { filename: snapshotFile });
  return context.window.PENGUIN_CONVEXITY_C24;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function renderDetail(frontScript, snapshot, search) {
  const main = { className: "", innerHTML: "" };
  const back = { onclick: null };
  const storage = new Map();
  const document = {
    querySelector(selector) {
      if (selector === ".c19-detail-main" || selector === "main") return main;
      if (selector === "[data-c24-back]") return back;
      return null;
    },
    querySelectorAll() { return []; },
  };
  const context = {
    window: { PENGUIN_CONVEXITY_C24: snapshot, scrollY: 0 },
    document,
    location: { pathname: "/project-detail.html", search },
    sessionStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
      removeItem(key) { storage.delete(key); },
    },
    requestAnimationFrame(callback) { callback(); },
    scrollTo() {},
    URLSearchParams,
    Date,
    Intl,
    Map,
    Set,
    Math,
    Number,
    String,
    Object,
    Array,
    JSON,
  };
  vm.runInNewContext(frontScript, context, { filename: frontScriptPath });
  return main.innerHTML;
}

function expectedHeading(item) {
  return `<h1>${escapeHtml(item.canonicalName)} <small>${escapeHtml(item.symbol)}</small></h1>`;
}

function fail(message) {
  process.stderr.write(`C2.5 X01 FAIL: ${message}\n`);
  process.exitCode = 1;
}

if (!fs.existsSync(frontScriptPath)) throw new Error(`front script not found: ${frontScriptPath}`);
if (!fs.existsSync(snapshotPath)) throw new Error(`snapshot not found: ${snapshotPath}`);

const frontScript = fs.readFileSync(frontScriptPath, "utf8");
const snapshot = loadSnapshot(snapshotPath);
const items = snapshot?.items || [];
const requiredNames = ["Wrapped Gonka", "cap", "OpenServ", "HALO"];
const missingRequired = requiredNames.filter((name) => !items.some((item) => item.canonicalName === name));

if (snapshot?.schemaVersion !== "c2.4-public-snapshot-v1" || !snapshot?.isComplete) {
  fail("snapshot is not the complete C2.4 public snapshot");
}
if (items.length !== 70) fail(`expected 70 public items, got ${items.length}`);
if (missingRequired.length) fail(`missing required regression items: ${missingRequired.join(", ")}`);

const hrefs = new Set();
const mismatches = [];
for (const item of items) {
  const expectedHref = `project-detail.html?assetId=${item.assetId}`;
  if (item.detailHref !== expectedHref) {
    mismatches.push(`${item.canonicalName}: detailHref ${item.detailHref} != ${expectedHref}`);
    continue;
  }
  if (hrefs.has(item.detailHref)) mismatches.push(`${item.canonicalName}: duplicate detailHref ${item.detailHref}`);
  hrefs.add(item.detailHref);

  const search = new URL(item.detailHref, "http://127.0.0.1/").search;
  const navigatedAssetId = new URLSearchParams(search).get("assetId");
  if (navigatedAssetId !== item.assetId) {
    mismatches.push(`${item.canonicalName}: navigation assetId ${navigatedAssetId} != ${item.assetId}`);
    continue;
  }
  const html = renderDetail(frontScript, snapshot, search);
  if (!html.includes(expectedHeading(item))) {
    mismatches.push(`${item.canonicalName}: ${item.assetId} rendered a different detail heading`);
  }
}

if (hrefs.size !== items.length) mismatches.push(`unique detailHref count ${hrefs.size} != ${items.length}`);
if (mismatches.length) {
  fail(`${mismatches.length} identity mismatches\n${mismatches.slice(0, 20).join("\n")}`);
} else {
  process.stdout.write(`C2.5 X01 PASS: ${items.length}/${items.length} card href, navigation assetId, and detail heading identities match; required cases=${requiredNames.join(", ")}\n`);
}
