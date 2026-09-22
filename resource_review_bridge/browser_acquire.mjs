import dns from "node:dns/promises";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { hasOriginalContent, snapshotFingerprint } from "./browser_rules.mjs";

const require = createRequire(import.meta.url);
const playwrightPath = process.env.RESOURCE_REVIEW_PLAYWRIGHT_PATH || "playwright";
const { chromium } = require(playwrightPath);

const targetUrl = process.argv[2];
const outputDirectory = process.argv[3];
const MAX_TEXT = 60000;
const MAX_CAROUSEL_ITEMS = 12;
const MAX_OUTBOUND_PAGES = 3;
const dnsCache = new Map();

function isPublicAddress(value) {
  if (value.includes(":")) {
    const lower = value.toLowerCase();
    return !(lower === "::1" || lower.startsWith("fe80:") || lower.startsWith("fc") || lower.startsWith("fd"));
  }
  const parts = value.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false;
  return !(
    parts[0] === 10 || parts[0] === 127 || parts[0] === 0 ||
    (parts[0] === 169 && parts[1] === 254) ||
    (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) ||
    (parts[0] === 192 && parts[1] === 168) ||
    parts[0] >= 224
  );
}

async function hostIsPublic(hostname) {
  const key = hostname.toLowerCase();
  if (key === "localhost" || key.endsWith(".localhost")) return false;
  if (dnsCache.has(key)) return dnsCache.get(key);
  const promise = dns.lookup(key, { all: true }).then((rows) => rows.length > 0 && rows.every((row) => isPublicAddress(row.address))).catch(() => false);
  dnsCache.set(key, promise);
  return promise;
}

async function guardRoute(route) {
  const url = route.request().url();
  if (/^(data|blob|about):/i.test(url)) return route.continue();
  let parsed;
  try { parsed = new URL(url); } catch { return route.abort("blockedbyclient"); }
  if (!["http:", "https:"].includes(parsed.protocol) || !(await hostIsPublic(parsed.hostname))) {
    return route.abort("blockedbyclient");
  }
  return route.continue();
}

async function clickFirstVisible(locator) {
  const count = Math.min(await locator.count(), 10);
  for (let index = 0; index < count; index += 1) {
    const item = locator.nth(index);
    if (await item.isVisible().catch(() => false)) {
      await item.click({ timeout: 3000 }).catch(() => {});
      return true;
    }
  }
  return false;
}

async function dismissAuthPrompt(page, platform) {
  const dialogVisible = await page.locator('[role="dialog"]').first().isVisible().catch(() => false);
  if (!dialogVisible) return "not_applicable";
  const closeSelectors = [
    'button[aria-label="Close"]', 'button[aria-label="close"]',
    '[role="button"][aria-label="Close"]', 'svg[aria-label="Close"]',
  ];
  for (const selector of closeSelectors) {
    const target = page.locator(selector);
    if (await clickFirstVisible(target)) {
      await page.waitForTimeout(500);
      return "completed";
    }
  }
  const closeText = page.getByRole("button", { name: /^(close|not now|取消|關閉)$/i });
  if (await clickFirstVisible(closeText)) {
    await page.waitForTimeout(500);
    return "completed";
  }
  if (platform === "threads") {
    const clicked = await page.evaluate(() => {
      const dialog = document.querySelector('[role="dialog"]');
      if (!dialog) return false;
      const box = dialog.getBoundingClientRect();
      const points = [[8, 8], [window.innerWidth - 8, 8], [8, window.innerHeight - 8], [window.innerWidth - 8, window.innerHeight - 8]];
      for (const [x, y] of points) {
        if (x >= box.left && x <= box.right && y >= box.top && y <= box.bottom) continue;
        const element = document.elementFromPoint(x, y);
        if (!element || element.closest('a,button,input,select,textarea,[role="button"]')) continue;
        element.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: x, clientY: y }));
        return true;
      }
      return false;
    });
    if (clicked) {
      await page.waitForTimeout(500);
      if (!(await page.locator('[role="dialog"]').first().isVisible().catch(() => false))) return "completed";
    }
  }
  return "blocked";
}

async function visibleSnapshot(page) {
  return page.evaluate((maxText) => {
    const text = (document.body?.innerText || "").replace(/\n{3,}/g, "\n\n").slice(0, maxText);
    const links = [...document.querySelectorAll("a[href]")].slice(0, 200).map((link) => ({
      text: (link.innerText || link.getAttribute("aria-label") || "").trim().slice(0, 500),
      url: link.href,
    })).filter((entry) => /^https?:/i.test(entry.url));
    return { title: document.title, url: location.href, text, links };
  }, MAX_TEXT);
}

async function contentSignals(page) {
  return page.evaluate(() => ({
    mainCount: document.querySelectorAll("main").length,
    articleCount: document.querySelectorAll("article").length,
    ogDescriptionLength: (document.querySelector('meta[property="og:description"]')?.content || "").trim().length,
  }));
}

async function captureCarousel(page) {
  const items = [];
  const seen = new Set();
  let duplicateStopped = false;
  for (let index = 0; index < MAX_CAROUSEL_ITEMS; index += 1) {
    const snapshot = await visibleSnapshot(page);
    const imagePath = path.join(outputDirectory, `page-${index + 1}.png`);
    const screenshot = await page.screenshot({ path: imagePath, fullPage: false }).catch(() => null);
    const fingerprint = snapshotFingerprint(snapshot, screenshot);
    if (seen.has(fingerprint)) {
      duplicateStopped = true;
      await fs.unlink(imagePath).catch(() => {});
      break;
    }
    seen.add(fingerprint);
    items.push({ index: index + 1, ...snapshot, screenshot: imagePath });
    const next = page.locator('button[aria-label="Next"], [role="button"][aria-label="Next"], button[aria-label="下一步"], button[aria-label="下一張"]');
    if (!(await clickFirstVisible(next))) break;
    await page.waitForTimeout(700);
  }
  return { items, bounded: items.length === MAX_CAROUSEL_ITEMS, duplicateStopped };
}

function outboundCandidates(snapshot, sourceHost) {
  const seen = new Set();
  const output = [];
  for (const link of snapshot.links || []) {
    try {
      let candidate = new URL(link.url);
      if (/^l\.instagram\.com$/i.test(candidate.hostname) && candidate.searchParams.get("u")) candidate = new URL(candidate.searchParams.get("u"));
      if (candidate.hostname === sourceHost || candidate.hostname.endsWith("." + sourceHost)) continue;
      const key = candidate.href.split("#")[0];
      if (seen.has(key)) continue;
      seen.add(key);
      output.push({ text: link.text, url: key });
    } catch {}
  }
  return output.slice(0, MAX_OUTBOUND_PAGES);
}

async function main() {
  await fs.mkdir(outputDirectory, { recursive: true, mode: 0o700 });
  const source = new URL(targetUrl);
  const platform = source.hostname.includes("instagram.com") ? "instagram" : /(^|\.)threads\.(net|com)$/i.test(source.hostname) ? "threads" : "web";
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: false, locale: "en-US", viewport: { width: 1280, height: 900 } });
  await context.route("**/*", guardRoute);
  const page = await context.newPage();
  const limitations = [];
  try {
    await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.waitForTimeout(1500);
    const authPrompt = await dismissAuthPrompt(page, platform);
    if (authPrompt === "blocked") limitations.push("A visible authentication overlay could not be dismissed safely.");
    const carousel = await captureCarousel(page);
    if (carousel.bounded) limitations.push(`Carousel inspection stopped at the ${MAX_CAROUSEL_ITEMS}-item limit.`);
    if (carousel.duplicateStopped) limitations.push("Carousel inspection stopped when the next control did not reveal a new item.");
    const first = carousel.items[0] || { text: "", links: [] };
    const outbound = [];
    for (const link of outboundCandidates(first, source.hostname)) {
      if (!(await hostIsPublic(new URL(link.url).hostname))) continue;
      const linkedPage = await context.newPage();
      try {
        await linkedPage.goto(link.url, { waitUntil: "domcontentloaded", timeout: 30000 });
        await linkedPage.waitForTimeout(500);
        outbound.push({ source_text: link.text, ...await visibleSnapshot(linkedPage) });
      } catch {
        limitations.push(`A direct outbound link could not be read: ${new URL(link.url).origin}`);
      } finally {
        await linkedPage.close();
      }
    }
    const signals = await contentSignals(page);
    const originalRead = hasOriginalContent(platform, page.url(), first, signals) ? "completed" : "blocked";
    if (originalRead === "blocked") limitations.push("The original page exposed insufficient visible text for review.");
    if (originalRead === "completed") limitations.push("Comments were limited to the important visible set rendered in the public page.");
    const result = {
      platform,
      requested_url: targetUrl,
      final_url: page.url(),
      auth_prompt: authPrompt,
      original_content: originalRead,
      carousel: carousel.items.length > 1 ? (carousel.bounded ? "partial" : "completed") : "not_applicable",
      comments: originalRead === "completed" ? "partial" : "blocked",
      outbound_links: outbound.length ? "completed" : "not_applicable",
      pages: carousel.items,
      outbound,
      limitations,
    };
    process.stdout.write(JSON.stringify(result));
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(String(error?.stack || error));
  process.exitCode = 1;
});
