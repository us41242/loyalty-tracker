// Stealth browser with human-like behavior
const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');

puppeteer.use(StealthPlugin());

// ── Human-like delays ───────────────────────────────────────────────────────
function randomDelay(min = 800, max = 2500) {
  const delay = Math.floor(Math.random() * (max - min + 1)) + min;
  return new Promise(resolve => setTimeout(resolve, delay));
}

function typingDelay() {
  return Math.floor(Math.random() * 150) + 50; // 50-200ms between keystrokes
}

// ── Human-like typing ───────────────────────────────────────────────────────
async function humanType(page, selector, text) {
  await page.waitForSelector(selector, { timeout: 15000 });
  await page.click(selector);
  await randomDelay(300, 700);

  for (const char of text) {
    await page.keyboard.type(char, { delay: typingDelay() });
  }
  await randomDelay(200, 500);
}

// ── Human-like click ────────────────────────────────────────────────────────
async function humanClick(page, selector) {
  await page.waitForSelector(selector, { timeout: 15000 });

  // Get element position
  const element = await page.$(selector);
  const box = await element.boundingBox();

  if (box) {
    // Click near center with slight random offset
    const x = box.x + box.width / 2 + (Math.random() * 6 - 3);
    const y = box.y + box.height / 2 + (Math.random() * 6 - 3);

    // Move mouse first, then click
    await page.mouse.move(x, y, { steps: Math.floor(Math.random() * 10) + 5 });
    await randomDelay(100, 300);
    await page.mouse.click(x, y);
  } else {
    await page.click(selector);
  }

  await randomDelay(500, 1500);
}

// ── Random scroll to simulate reading ───────────────────────────────────────
async function humanScroll(page, amount = null) {
  const scrollAmount = amount || Math.floor(Math.random() * 400) + 200;
  await page.evaluate((px) => {
    window.scrollBy({ top: px, behavior: 'smooth' });
  }, scrollAmount);
  await randomDelay(500, 1500);
}

// ── Launch stealth browser ──────────────────────────────────────────────────
async function launchBrowser() {
  const browser = await puppeteer.launch({
    headless: process.env.CI === 'true' ? 'new' : false,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-blink-features=AutomationControlled',
      '--disable-features=IsolateOrigins,site-per-process',
      '--window-size=1920,1080',
    ],
    defaultViewport: { width: 1920, height: 1080 },
  });

  return browser;
}

// ── Create page with stealth settings ───────────────────────────────────────
async function createPage(browser) {
  const page = await browser.newPage();

  // Set realistic user agent
  await page.setUserAgent(
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  );

  // Set realistic viewport
  await page.setViewport({ width: 1920, height: 1080 });

  // Set language and platform
  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(navigator, 'language', { get: () => 'en-US' });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
  });

  return page;
}

// ── Navigate with human-like behavior ───────────────────────────────────────
async function humanNavigate(page, url, options = {}) {
  await page.goto(url, {
    waitUntil: 'networkidle2',
    timeout: 30000,
    ...options,
  });
  await randomDelay(1500, 3500);
  // Random small scroll to simulate reading
  await humanScroll(page, Math.floor(Math.random() * 200) + 50);
}

module.exports = {
  launchBrowser,
  createPage,
  humanType,
  humanClick,
  humanScroll,
  humanNavigate,
  randomDelay,
};
