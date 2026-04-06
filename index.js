require('dotenv').config();
const { chromium } = require('playwright');
const { scrapeCaesars } = require('./scrapers/caesars');
const { scrapeRio } = require('./scrapers/rio');
const { scrapeMGM } = require('./scrapers/mgm');

async function main() {
  const startTime = Date.now();
  console.log('🚀 Casino Rewards Scraper');
  console.log(`   ${new Date().toLocaleString('en-US', { timeZone: 'America/Los_Angeles' })} PT\n`);

  const browser = await chromium.launch({
    headless: process.env.CI === 'true',  // headless in CI, visible locally
    slowMo: process.env.CI === 'true' ? 0 : 300,
  });

  try {
    // Run scrapers sequentially (each manages its own browser context)
    await scrapeCaesars(browser);
    await scrapeRio(browser);
    await scrapeMGM(browser);
  } catch (err) {
    console.error('\n💥 Fatal error:', err.message);
    process.exit(1);
  } finally {
    await browser.close();
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    console.log(`\n🏁 Done in ${elapsed}s`);
  }
}

main();
