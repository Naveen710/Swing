import { chromium } from "playwright";
const B = process.env.E2E_BASE_URL ?? "http://localhost:3000";
const out = process.env.E2E_OUT ?? "e2e-screenshots";
await import("node:fs").then((fs) => fs.mkdirSync(out, { recursive: true }));
const b = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const page = await b.newPage({ viewport: { width: 1400, height: 950 } });
const errors = [];
page.on("pageerror", (e) => errors.push("pageerror " + e.message));
page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("404")) errors.push("console " + m.text().slice(0, 200)); });
page.on("dialog", (d) => d.accept("on track"));
const step = async (name, fn) => { try { await fn(); console.log("PASS", name); } catch (e) { console.log("FAIL", name, String(e).slice(0, 300)); await page.screenshot({ path: `${out}/fail-${name}.png` }); } };

await step("dashboard-empty", async () => {
  await page.goto(B + "/research");
  await page.waitForSelector("text=Nothing researched yet", { timeout: 20000 });
  await page.screenshot({ path: `${out}/01-dashboard-empty.png` });
});
await step("seed", async () => {
  await page.click("button:has-text('Load sample companies')");
  await page.waitForSelector("text=Nothing researched yet", { state: "detached", timeout: 90000 });
  await page.waitForSelector(".rs-stat b", { timeout: 10000 });
  await page.screenshot({ path: `${out}/02-dashboard.png`, fullPage: true });
});
await step("explorer-search", async () => {
  await page.goto(B + "/research/companies");
  await page.fill(".rs-search", "Aravali");
  await page.waitForSelector("a.stock-link:has-text('SMPCABLE')", { timeout: 10000 });
  await page.fill(".rs-search", "RELIANCE");
  await page.waitForSelector("a.stock-link:has-text('RELIANCE')", { timeout: 10000 });
  await page.screenshot({ path: `${out}/03-explorer.png` });
});
await step("dossier-overview", async () => {
  await page.goto(B + "/research/company/SMPCABLE");
  await page.waitForSelector("text=Seven dimensions", { timeout: 20000 });
  await page.waitForSelector(".rs-sample");
  await page.screenshot({ path: `${out}/04-dossier.png`, fullPage: true });
});
for (const [tab, sel] of [["Financials", "text=Revenue, profit and margin"], ["Valuation", "text=Bear / base / bull"], ["Price", ".recharts-line"], ["Filings & news", "text=Exchange filings"], ["Data & provenance", "text=Snapshot history"]]) {
  await step(`tab-${tab}`, async () => {
    await page.click(`.rs-tab:has-text('${tab}')`);
    await page.waitForSelector(sel, { timeout: 15000 });
    await page.screenshot({ path: `${out}/05-tab-${tab.replace(/\W+/g, "")}.png`, fullPage: true });
  });
}
await step("replay", async () => {
  await page.click("button:has-text('Replay')");
  await page.waitForSelector("text=reproduced exactly", { timeout: 10000 });
});
await step("evidence-metric-to-fact-to-source", async () => {
  await page.click(".rs-tab:has-text('Overview')");
  await page.click(".rs-claim--computed .rs-ev >> nth=0");
  await page.waitForSelector("text=Input facts", { timeout: 10000 });
  await page.screenshot({ path: `${out}/06-evidence-metric.png`, fullPage: true });
  await page.click("a:has-text('fact:') >> nth=0");
  await page.waitForSelector("text=SHA-256", { timeout: 10000 });
  await page.screenshot({ path: `${out}/07-evidence-fact.png`, fullPage: true });
});
await step("evidence-filing-document", async () => {
  await page.goto(B + "/research/company/SMPCABLE");
  await page.waitForSelector("text=Catalysts and filings");
  await page.click(".rs-claim--sourced .rs-ev >> nth=0");
  await page.waitForSelector("text=Page 1", { timeout: 10000 });
  await page.screenshot({ path: `${out}/08-evidence-filing.png`, fullPage: true });
});
await step("watchlist-add-from-dossier", async () => {
  await page.goto(B + "/research/company/SMPCABLE");
  await page.waitForSelector("text=Seven dimensions");
  await page.click("button:has-text('Add to watchlist')");
  await page.fill(".rs-pop textarea", "Orders accelerating, price lagging");
  await page.click(".rs-pop button:has-text('Add')");
  await page.waitForSelector("text=Added.", { timeout: 10000 });
  await page.goto(B + "/research/watchlists");
  await page.waitForSelector("text=Orders accelerating, price lagging", { timeout: 10000 });
  await page.screenshot({ path: `${out}/09-watchlists.png` });
});
await step("journal", async () => {
  await page.goto(B + "/research/journal?new=SMPCABLE");
  await page.waitForSelector("text=New journal entry");
  await page.fill("input[placeholder='One line']", "Transmission order cycle");
  await page.fill("textarea >> nth=0", "Order inflow from state utilities is accelerating; market still prices it as a slow grower.");
  await page.fill("textarea >> nth=4", "Order inflow falls YoY for two quarters, or EBIT margin drops below 12%.");
  await page.click("form button:has-text('Save')");
  await page.waitForSelector("text=Transmission order cycle", { timeout: 10000 });
  await page.fill("input[placeholder='What did you check?']", "Checked Q1 order announcement");
  await page.click("button:has-text('Add follow-up')");
  await page.waitForSelector("text=Checked Q1 order announcement", { timeout: 10000 });
  await page.screenshot({ path: `${out}/10-journal.png`, fullPage: true });
});
await step("discovery", async () => {
  await page.goto(B + "/research/discovery");
  await page.waitForSelector(".rs-queue tbody tr", { timeout: 10000 });
  await page.click(".rs-th-btn:has-text('Recognition gap')");
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}/11-discovery.png` });
});
await step("news", async () => {
  await page.goto(B + "/research/news");
  await page.waitForSelector("text=Also reported by", { timeout: 10000 });
  await page.click(".rs-tab:has-text('Exchange filings')");
  await page.waitForSelector(".rs-evt--governance", { timeout: 10000 });
  await page.screenshot({ path: `${out}/12-news.png` });
});
await step("data-quality", async () => {
  await page.goto(B + "/research/data");
  await page.waitForSelector("text=Verification checks", { timeout: 10000 });
  await page.screenshot({ path: `${out}/13-data.png`, fullPage: true });
});
await step("settings", async () => {
  await page.goto(B + "/research/settings");
  await page.waitForSelector("text=Data sources", { timeout: 10000 });
  await page.screenshot({ path: `${out}/14-settings.png`, fullPage: true });
});
await step("segment-buttons", async () => {
  await page.goto(B + "/research");
  await page.waitForSelector(".rs-seg--large .rs-seg-count:has-text('100')", { timeout: 15000 });
  for (const [k, n] of [["mid", "150"], ["small", "250"]]) await page.waitForSelector(`.rs-seg--${k} .rs-seg-count:has-text('${n}')`);
  await page.screenshot({ path: `${out}/15-segment-buttons.png` });
});
await step("segment-large-fetch", async () => {
  await page.click(".rs-seg--large");
  await page.waitForURL("**/research/segment/large", { timeout: 15000 });
  await page.waitForSelector("text=Last fetch finished", { timeout: 120000 });        // job finished and page reloaded
  await page.waitForFunction(() => document.querySelectorAll(".rs-seg-tbl tbody tr").length === 100, null, { timeout: 30000 });
  await page.waitForFunction(() => [...document.querySelectorAll(".rs-stat")].some((e) => e.textContent?.startsWith("Researched100")), null, { timeout: 20000 });
  await page.screenshot({ path: `${out}/16-segment-large.png`, fullPage: false });
});
await step("segment-sort-filter-template", async () => {
  await page.click(".rs-sort:has-text('Company')");
  const first = await page.textContent(".rs-seg-tbl tbody tr:first-child .stock-link");
  if (first !== "ABB") throw new Error("sort by symbol gave " + first);
  await page.fill(".rs-search", "infosys");
  await page.waitForFunction(() => document.querySelectorAll(".rs-seg-tbl tbody tr").length === 1);
  const [dl] = await Promise.all([page.waitForEvent("download"), page.click("button:has-text('Verification template')")]);
  if (dl.suggestedFilename() !== "real_cases_large_cap.csv") throw new Error(dl.suggestedFilename());
});
await step("segment-tabs-small", async () => {
  await page.click(".rs-tab:has-text('Small cap')");
  await page.waitForURL("**/research/segment/small");
  await page.waitForFunction(() => document.querySelectorAll(".rs-seg-tbl tbody tr").length === 250, null, { timeout: 30000 });
  await page.click("button:has-text('Fetch latest data')");
  await page.waitForSelector(".rs-progress", { timeout: 10000 });
  await page.screenshot({ path: `${out}/17-segment-small-progress.png` });
  await page.waitForSelector("text=Fetch latest data", { timeout: 120000 });
  await page.waitForSelector("text=Last fetch finished", { timeout: 20000 });
  await page.screenshot({ path: `${out}/18-segment-small-done.png` });
});
const phone = await b.newPage({ viewport: { width: 390, height: 844 } });
for (const p of ["/research", "/research/company/SMPCABLE", "/research/discovery", "/research/journal", "/research/data", "/research/segment/mid"]) {
  await phone.goto(B + p);
  await phone.waitForTimeout(2500);
  const w = await phone.evaluate(() => document.documentElement.scrollWidth);
  console.log("phone", p, "scrollWidth", w);
  await phone.screenshot({ path: `${out}/phone-${p.replace(/\W+/g, "_")}.png` });
}
console.log("errors:", errors);
await b.close();
