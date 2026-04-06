require('dotenv').config();
const { launchBrowser } = require('./scrapers/browser');
const { scrapeCaesars } = require('./scrapers/caesars');
const { scrapeRio } = require('./scrapers/rio');
const { scrapeMGM } = require('./scrapers/mgm');

async function main() {
  const startTime = Date.now();
  console.log('🚀 Casino Rewards Scraper (Stealth Mode)');
  console.log(`   ${new Date().toLocaleString('en-US', { timeZone: 'America/Los_Angeles' })} PT\n`);

  const browser = await launchBrowser();

  try {
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
