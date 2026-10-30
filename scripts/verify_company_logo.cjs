const { chromium } = require("@playwright/test");
const fs = require("fs");
const path = require("path");
const assert = require("assert/strict");
const zlib = require("zlib");

const base = "http://127.0.0.1:5037";
const output = path.join(".tmp", "company-logo-validation");
fs.mkdirSync(output, { recursive: true });

function crc32(buffer) {
    let crc = 0xffffffff;
    for (const byte of buffer) {
        crc ^= byte;
        for (let i = 0; i < 8; i += 1) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
    return (~crc) >>> 0;
}

function chunk(type, data) {
    const body = Buffer.concat([Buffer.from(type), data]);
    const out = Buffer.alloc(12 + data.length);
    out.writeUInt32BE(data.length, 0);
    body.copy(out, 4);
    out.writeUInt32BE(crc32(body), 8 + data.length);
    return out;
}

function syntheticLogo() {
    const width = 180;
    const height = 100;
    const rows = [];
    for (let y = 0; y < height; y += 1) {
        const row = Buffer.alloc(1 + width * 4);
        for (let x = 0; x < width; x += 1) {
            const i = 1 + x * 4;
            const hole = (x - 90) * (x - 90) + (y - 50) * (y - 50) < 28 * 28;
            row[i] = hole ? 248 : 22;
            row[i + 1] = hole ? 250 : 163;
            row[i + 2] = hole ? 252 : 74;
            row[i + 3] = hole ? 0 : 255;
        }
        rows.push(row);
    }
    const ihdr = Buffer.alloc(13);
    ihdr.writeUInt32BE(width, 0);
    ihdr.writeUInt32BE(height, 4);
    ihdr[8] = 8;
    ihdr[9] = 6;
    return Buffer.concat([
        Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
        chunk("IHDR", ihdr),
        chunk("IDAT", zlib.deflateSync(Buffer.concat(rows))),
        chunk("IEND", Buffer.alloc(0)),
    ]);
}

(async () => {
    const logoPath = path.join(output, "synthetic-logo.png");
    fs.writeFileSync(logoPath, syntheticLogo());
    const browser = await chromium.launch({ headless: true, channel: "msedge" });
    const context = await browser.newContext({
        viewport: { width: 1600, height: 900 },
        deviceScaleFactor: 1,
        ignoreHTTPSErrors: true,
    });
    const response = await context.request.post(base + "/api/auth/login", {
        data: { identifier: "core_admin", password: "SecretCorePass999" },
    });
    assert.equal(response.status(), 200);
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));

    const snap = async (name) => page.screenshot({ path: path.join(output, name + ".png"), fullPage: false });
    const brandSrc = async () => page.locator("[data-company-logo] img").getAttribute("src");
    const hasDefault = async () => page.locator("[data-company-logo] .ti-tool").count();
    const noOverflow = async () => page.evaluate(() => {
        const brand = document.querySelector(".ix-brand");
        const box = document.querySelector("[data-company-logo]");
        return {
            page: document.documentElement.scrollWidth <= innerWidth + 1,
            brand: brand.scrollWidth <= brand.clientWidth + 1,
            box: Math.round(box.getBoundingClientRect().width) <= 48,
            height: Math.round(box.getBoundingClientRect().height) <= 51,
        };
    });

    await page.goto(base + "/dashboard", { waitUntil: "networkidle" });
    assert.equal(await hasDefault(), 1);
    assert.equal(await page.locator("[data-company-logo] img").count(), 0);
    await snap("company-logo-default-fallback");

    await page.goto(base + "/settings?tab=general", { waitUntil: "networkidle" });
    const picker = page.waitForEvent("filechooser");
    await page.locator("#st-logo-choose").click();
    await (await picker).setFiles({
        name: "logo-empresa.png",
        mimeType: "image/png",
        buffer: fs.readFileSync(logoPath),
    });
    await page.waitForFunction(() => document.querySelector("#st-logo-preview").src.startsWith("blob:"));
    await page.locator("#st-save").click();
    await page.waitForFunction(() => document.querySelector("#st-logo-name").textContent === "Logo actual");
    await page.locator("[data-company-logo] img").evaluate((img) => img.decode());
    const savedSrc = await brandSrc();
    assert(savedSrc.includes("/api/settings/company-logo?version="));
    assert.equal(await page.locator("#st-logo-preview").getAttribute("src"), savedSrc);
    await snap("company-logo-settings");
    await snap("company-logo-sidebar-settings");

    const pages = [
        ["/dashboard", "company-logo-sidebar-dashboard"],
        ["/products", "company-logo-sidebar-inventory"],
        ["/purchases", "company-logo-sidebar-purchases"],
    ];
    for (const [url, name] of pages) {
        await page.goto(base + url, { waitUntil: "networkidle" });
        await page.locator("[data-company-logo] img").evaluate((img) => img.decode());
        assert.equal(await brandSrc(), savedSrc);
        assert.equal(await hasDefault(), 0);
        await snap(name);
    }

    for (const [width, height] of [[1600, 900], [1366, 768], [1280, 720]]) {
        await page.setViewportSize({ width, height });
        for (const url of ["/settings?tab=general", "/dashboard", "/products", "/purchases"]) {
            await page.goto(base + url, { waitUntil: "networkidle" });
            await page.locator("[data-company-logo] img").evaluate((img) => img.decode());
            const geometry = await noOverflow();
            assert(geometry.page && geometry.brand && geometry.box && geometry.height, `${url} ${width}x${height} ${JSON.stringify(geometry)}`);
            assert.equal(await brandSrc(), savedSrc);
        }
    }

    await page.setViewportSize({ width: 1600, height: 900 });
    await page.goto(base + "/settings?tab=general", { waitUntil: "networkidle" });
    await page.locator("#st-logo-delete").click();
    await page.locator("#st-confirm[open]").waitFor();
    await page.locator("#st-confirm-accept").click();
    await page.waitForFunction(() => document.querySelector("#st-logo-preview").hidden);
    assert.equal(await hasDefault(), 1);
    assert.equal(await page.locator("[data-company-logo] img").count(), 0);

    await page.goto(base + "/settings?ref=1", { waitUntil: "networkidle" });
    assert.equal(await page.locator("#st-logo-file").count(), 0);
    assert.equal(await hasDefault(), 1);
    assert.equal(await page.locator("[data-company-logo] img").count(), 0);
    assert.deepEqual(errors, []);
    console.log("PASS: default, custom shell, settings preview parity, viewports, delete, reference; JS errors: 0");
    await browser.close();
})().catch((error) => {
    console.error(error);
    process.exit(1);
});
