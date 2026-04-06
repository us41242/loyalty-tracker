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

// ── React-friendly typing ───────────────────────────────────────────────────
// React controlled inputs need native input events to update state.
// Standard puppeteer keyboard.type dispatches keydown/keypress/keyup but
// React listens on the native input event via its synthetic event system.
async function reactType(page, selector, text) {
  await page.waitForSelector(selector, { timeout: 15000 });

  // Click to focus
  const el = await page.$(selector);
  await el.click({ clickCount: 3 }); // select all existing text
  await randomDelay(200, 400);
  await page.keyboard.press('Backspace'); // clear
  await randomDelay(200, 400);

  // Type each character and trigger React-compatible events
  for (const char of text) {
    await page.keyboard.type(char, { delay: typingDelay() });
  }

  // Force React to pick up the value by dispatching native events
  await page.evaluate((sel, val) => {
    const input = document.querySelector(sel);
    if (input) {
      // Use React's internal value setter to bypass controlled component
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
      ).set;
      nativeInputValueSetter.call(input, val);

      // Dispatch events React listens to
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }, selector, text);

  await randomDelay(300, 600);
}

// ── Human-like typing (non-React) ───────────────────────────────────────────
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

  const element = await page.$(selector);
  const box = await element.boundingBox();

  if (box) {
    const x = box.x + box.width / 2 + (Math.random() * 6 - 3);
    const y = box.y + box.height / 2 + (Math.random() * 6 - 3);

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
      '--disable-web-security',
      '--disable-features=CrossSiteDocumentBlockingIfIsolating',
      '--window-size=1920,1080',
      '--lang=en-US,en',
    ],
    defaultViewport: { width: 1920, height: 1080 },
    ignoreDefaultArgs: ['--enable-automation'],
  });

  return browser;
}

// ── Create page with stealth settings ───────────────────────────────────────
async function createPage(browser) {
  const page = await browser.newPage();

  // Set realistic user agent
  await page.setUserAgent(
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
  );

  await page.setViewport({ width: 1920, height: 1080 });

  // Extra stealth: override webdriver, plugins, permissions
  await page.evaluateOnNewDocument(() => {
    // Hide webdriver
    Object.defineProperty(navigator, 'webdriver', { get: () => false });

    // Language
    Object.defineProperty(navigator, 'language', { get: () => 'en-US' });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });

    // Fake plugins
    Object.defineProperty(navigator, 'plugins', {
      get: () => [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
        { name: 'Native Client', filename: 'internal-nacl-plugin' },
      ],
    });

    // Fake permissions
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) =>
      parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);

    // Chrome runtime
    window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {} };

    // WebGL vendor
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
      if (parameter === 37445) return 'Intel Inc.';
      if (parameter === 37446) return 'Intel Iris OpenGL Engine';
      return getParameter.call(this, parameter);
    };
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
  await humanScroll(page, Math.floor(Math.random() * 200) + 50);
}

module.exports = {
  launchBrowser,
  createPage,
  humanType,
  reactType,
  humanClick,
  humanScroll,
  humanNavigate,
  randomDelay,
};
