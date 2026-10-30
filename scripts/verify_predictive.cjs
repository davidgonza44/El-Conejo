/* Capturas y overflow del módulo predictivo contra el servidor desechable:
   .\venv312\Scripts\python.exe scripts/test_predictive_module.py --serve
   (SQLite temporal en 127.0.0.1:5038; no toca ferreteria_conejo). */
const { chromium, expect } = require("@playwright/test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const origin = "http://127.0.0.1:5038";
const output = ".tmp/predictive-validation";
const pages = [
    ["/predictive/demand", "demand"],
    ["/predictive/replenishment", "replenishment"],
    ["/predictive/critical", "critical"],
    ["/predictive/trends", "trends"],
    ["/predictive/accuracy", "accuracy"],
];
const viewports = [[1600, 900], [1366, 768], [1280, 720]];
const fixtureMarks = ["3,245", "Prophet", "92.4%", "1,248", "87.4%"];

(async () => {
    const browser = await chromium.launch({ channel: "msedge" });
    const results = { screens: [], overflow: [], errors: [] };
    try {
        const context = await browser.newContext({
            viewport: { width: 1600, height: 900 },
            deviceScaleFactor: 1,
            locale: "es-VE",
        });
        const login = await context.request.post(origin + "/api/auth/login", {
            data: { identifier: "core_admin", password: "SecretCorePass999" },
        });
        assert.equal(login.status(), 200, await login.text());
        const page = await context.newPage();
        page.on("pageerror", (error) => results.errors.push(error.message));
        fs.mkdirSync(output, { recursive: true });

        const go = async (url) => {
            await page.goto(origin + url, { waitUntil: "networkidle" });
            await page.evaluate(() => document.fonts.ready);
        };
        const overflow = async (width) => {
            const geometry = await page.evaluate(() => ({
                scroll: document.documentElement.scrollWidth,
                inner: innerWidth,
                dpr: devicePixelRatio,
                zoom: visualViewport.scale,
            }));
            assert.equal(geometry.dpr, 1);
            assert.equal(geometry.zoom, 1);
            assert.ok(geometry.scroll <= width + 1, JSON.stringify(geometry));
            return geometry;
        };

        for (const [url, name] of pages) {
            await go(url);
            const body = await page.locator("main").innerText();
            for (const mark of fixtureMarks) {
                assert.equal(body.includes(mark), false, `${name} LIVE contiene fixture ${mark}`);
            }
            assert.ok(await page.locator(".px-page, .px-forecast, .px-kpis").count() > 0, `${name} sin shell`);
            const geometry = await overflow(1600);
            await page.screenshot({ path: path.join(output, `${name}-live-1600.png`) });
            results.screens.push({ name, mode: "live", ...geometry });
        }

        await go("/predictive/demand?ref=1");
        const refBody = await page.locator("main").innerText();
        assert.ok(refBody.includes("3,245"), "ref=1 no muestra fixture de demanda");
        await page.screenshot({ path: path.join(output, "demand-ref-1600.png") });

        await go("/predictive/accuracy?ref=1");
        const acc = await page.locator("main").innerText();
        assert.ok(acc.includes("92.4%") || acc.includes("7.6%"), "ref=1 precisión sin fixture");
        await page.screenshot({ path: path.join(output, "accuracy-ref-1600.png") });

        await go("/predictive/replenishment");
        const canOrder = await page.locator("#px-order-open").count();
        if (canOrder) {
            const first = page.locator("[data-order-pick]").first();
            if (await first.count()) {
                await first.check();
                await page.locator("#px-order-open").click();
                await expect(page.locator("#rl-order")).toBeVisible();
                await page.screenshot({ path: path.join(output, "replenishment-drawer-live.png") });
                await page.keyboard.press("Escape");
            }
        }

        for (const [width, height] of viewports) {
            await page.setViewportSize({ width, height });
            for (const [url, name] of pages) {
                await go(url);
                const geometry = await overflow(width);
                await page.screenshot({ path: path.join(output, `${name}-live-${width}.png`) });
                results.overflow.push({ name, width, height, ...geometry });
            }
        }

        assert.deepEqual(results.errors, []);
        fs.writeFileSync(path.join(output, "verification.json"), JSON.stringify({ playwright: "PASS", ...results }, null, 2));
        console.log("PASS", results.screens.length, "capturas LIVE 1600;", results.overflow.length, "overflow checks");
    } finally {
        await browser.close();
    }
})().catch((error) => {
    console.error(error);
    process.exit(1);
});
