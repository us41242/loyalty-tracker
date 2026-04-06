const { supabase, parseDate } = require('./db');
const { fetch2FACode } = require('./gmail');

async function scrapeCaesars(browser, gmailToken) {
  console.log('\n═══════════════════════════════════════');
  console.log('  CAESARS REWARDS SCRAPER');
  console.log('═══════════════════════════════════════\n');

  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });
  const page = await context.newPage();

  try {
    // 1. Login
    await login(page);

    // 2. Check for 2FA right after login
    await handle2FA(page, gmailToken);

    // 3. Scrape rewards home
    const rewards = await scrapeRewardsHome(page);

    // 4. Scrape reservations
    const pastRes = await scrapeReservations(page, 'past');
    const currentRes = await scrapeReservations(page, 'current');

    // 5. Scrape offers (first page only for daily check)
    const offers = await scrapeOffers(page);

    // 6. Scrape Great Gift (triggers 2FA)
    const greatGift = await scrapeGreatGift(page, gmailToken);
    rewards.greatGiftPoints = greatGift;

    // 7. Save to Supabase
    await saveSnapshot(rewards);
    await saveReservations([...pastRes, ...currentRes]);
    await saveOffers(offers);

    console.log('\n✅ Caesars scrape complete!\n');
  } catch (err) {
    console.error('❌ Caesars error:', err.message);
  } finally {
    await context.close();
  }
}

// ── Login ───────────────────────────────────────────────────────────────────
async function login(page) {
  console.log('🔑 Logging in...');
  await page.goto('https://www.caesars.com/myrewards/profile/signin/', {
    waitUntil: 'networkidle', timeout: 30000
  });

  await page.waitForSelector('input', { timeout: 15000 });
  await page.waitForTimeout(2000);

  // Username field
  const inputs = page.locator('input');
  const inputCount = await inputs.count();

  // Find the text/email input (not password)
  for (let i = 0; i < inputCount; i++) {
    const type = await inputs.nth(i).getAttribute('type');
    if (type === 'text' || type === 'email' || !type) {
      await inputs.nth(i).fill(process.env.CAESARS_USERNAME);
      break;
    }
  }

  // Password
  await page.locator('input[type="password"]').fill(process.env.CAESARS_PASSWORD);
  await page.waitForTimeout(500);

  // Submit
  await page.locator('button[type="submit"], button:has-text("SIGN IN"), button:has-text("Sign In")').first().click();

  await page.waitForTimeout(5000);
  console.log('  URL after login:', page.url());
}

// ── 2FA ─────────────────────────────────────────────────────────────────────
async function handle2FA(page, gmailToken) {
  if (!page.url().includes('/verification/step-up')) return;

  console.log('🔐 2FA required...');
  await page.waitForSelector('input[maxlength="1"]', { timeout: 10000 });

  // Wait a bit for the email to arrive
  await page.waitForTimeout(10000);

  const code = await fetch2FACode(gmailToken);
  if (!code) throw new Error('Could not get 2FA code');

  console.log(`  Entering code: ${code}`);
  const codeInputs = page.locator('input[maxlength="1"]');
  for (let i = 0; i < 6; i++) {
    await codeInputs.nth(i).fill(code[i]);
    await page.waitForTimeout(200);
  }

  await page.waitForTimeout(3000);
  await page.waitForNavigation({ waitUntil: 'networkidle', timeout: 30000 }).catch(() => {});
  console.log('  ✅ 2FA complete');
}

// ── Rewards Home ────────────────────────────────────────────────────────────
async function scrapeRewardsHome(page) {
  console.log('📊 Scraping rewards home...');
  await page.goto('https://www.caesars.com/rewards/home', {
    waitUntil: 'networkidle', timeout: 30000
  });
  await page.waitForTimeout(3000);

  const data = await page.evaluate(() => {
    const text = document.body.innerText;

    const rc = text.match(/([\d,]+)\s*REWARD CREDITS/i);
    const tc = text.match(/([\d,]+)\s*TIER CREDITS/i);
    const ts = text.match(/(SEVEN STARS|DIAMOND ELITE|DIAMOND PLUS|DIAMOND|PLATINUM|GOLD)/i);
    const tn = text.match(/([\d,]+)\s*to\s+(Seven Stars|Diamond Elite|Diamond Plus|Diamond|Platinum|Gold)/i);
    const le = text.match(/Last credits earned:\s*(\d{2}\/\d{2}\/\d{4})/i);
    const ce = text.match(/Earn more Reward Credits before\s*(\d{2}\/\d{2}\/\d{4})/i);

    return {
      rewardCredits: rc ? parseInt(rc[1].replace(/,/g, '')) : null,
      tierCredits: tc ? parseInt(tc[1].replace(/,/g, '')) : null,
      tierStatus: ts ? ts[1] : null,
      tierNext: tn ? tn[2] : null,
      tierCreditsNeeded: tn ? parseInt(tn[1].replace(/,/g, '')) : null,
      lastEarnedDate: le ? le[1] : null,
      creditsExpireDate: ce ? ce[1] : null,
    };
  });

  console.log('  Credits:', data.rewardCredits, '| Tier:', data.tierCredits, data.tierStatus);
  return data;
}

// ── Reservations ────────────────────────────────────────────────────────────
async function scrapeReservations(page, tab) {
  console.log(`📋 Scraping ${tab} reservations...`);
  await page.goto('https://www.caesars.com/rewards/stays', {
    waitUntil: 'networkidle', timeout: 30000
  });
  await page.waitForTimeout(2000);

  // Click tab
  const tabBtn = page.locator(`text=${tab.toUpperCase()}`).first();
  if (await tabBtn.isVisible()) {
    await tabBtn.click();
    await page.waitForTimeout(2000);
  }

  const reservations = await page.evaluate((currentTab) => {
    const cards = [];
    const text = document.body.innerText;
    const lines = text.split('\n').map(l => l.trim()).filter(Boolean);

    for (let i = 0; i < lines.length; i++) {
      if (lines[i] === 'Property') {
        const card = { tab: currentTab };
        for (let j = i; j < Math.min(i + 20, lines.length); j++) {
          if (lines[j] === 'Property') card.property = lines[j + 1] || null;
          if (lines[j] === 'Location') card.location = lines[j + 1] || null;
          if (lines[j] === 'Check-In') card.checkIn = lines[j + 1] || null;
          if (lines[j] === 'Checkout') card.checkOut = lines[j + 1] || null;
          if (lines[j] === 'Adults') card.adults = parseInt(lines[j + 1]) || null;
          if (lines[j] === 'Children') card.children = parseInt(lines[j + 1]) || null;
          if (lines[j] === 'Confirmation') card.confirmationCode = lines[j + 1] || null;
        }
        if (card.confirmationCode) cards.push(card);
      }
    }
    return cards;
  }, tab);

  console.log(`  Found ${reservations.length} ${tab} reservations`);
  return reservations;
}

// ── Offers ──────────────────────────────────────────────────────────────────
async function scrapeOffers(page) {
  console.log('🎁 Scraping offers...');
  await page.goto('https://www.caesars.com/rewards/offers', {
    waitUntil: 'networkidle', timeout: 30000
  });
  await page.waitForTimeout(3000);

  // Clear filters
  const clearBtn = page.locator('text=Clear Filters');
  if (await clearBtn.isVisible().catch(() => false)) {
    await clearBtn.click();
    await page.waitForTimeout(2000);
  }

  // Expand all "See More" sections
  for (let i = 0; i < 10; i++) {
    const seeMore = page.locator('button:has-text("See More"), a:has-text("See More")').first();
    if (await seeMore.isVisible().catch(() => false)) {
      await seeMore.click();
      await page.waitForTimeout(2000);
    } else {
      break;
    }
  }

  // Scrape all offer cards visible on the page
  const offers = await page.evaluate(() => {
    const results = [];
    const text = document.body.innerText;

    // Find section headers and their offers
    let currentSection = 'Unknown';
    const lines = text.split('\n').map(l => l.trim()).filter(Boolean);

    for (let i = 0; i < lines.length; i++) {
      // Detect section headers like "EXPIRING IN THE NEXT 7 DAYS (21)" or "APRIL OFFERS (58)"
      const sectionMatch = lines[i].match(/^(EXPIRING.*?|(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+OFFERS?)\s*\((\d+)\)/i);
      if (sectionMatch) {
        currentSection = sectionMatch[1].trim();
        continue;
      }

      // Detect offer lines — typically "Expires today" or "Valid MM.DD.YY - MM.DD.YY"
      const expiresMatch = lines[i].match(/Expires?\s+(today|tomorrow|\d.+)/i);
      const validMatch = lines[i].match(/Valid\s+(\d.+)/i);

      if (expiresMatch || validMatch) {
        // The title is usually 1-3 lines above
        let title = '';
        for (let j = i - 1; j >= Math.max(0, i - 4); j--) {
          if (lines[j].match(/^(EXPIRING|JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)/i)) break;
          if (lines[j].match(/^(See More|Clear Filters|FILTER|DESTINATIONS|DATES|OFFER TYPE)/i)) break;
          title = lines[j];
          break;
        }

        // Property is usually between title and dates
        let property = '';
        if (i > 1 && !lines[i - 1].match(/^[A-Z\$\d]/)) {
          property = lines[i - 1];
        }

        results.push({
          title: title || null,
          section: currentSection,
          property: property || null,
          dates: (expiresMatch || validMatch)[0],
        });
      }
    }

    return results;
  });

  // Click each offer to get offer ID and details
  // For efficiency on daily runs, only process new offers
  const detailedOffers = [];
  const offerCards = page.locator('[class*="offer"] a, [class*="Offer"] a, [data-testid*="offer"]');
  const cardCount = await offerCards.count().catch(() => 0);

  // For each visible card, try to click and get details
  for (let i = 0; i < Math.min(cardCount, 50); i++) {
    try {
      await offerCards.nth(i).click();
      await page.waitForTimeout(1000);

      const detail = await page.evaluate(() => {
        // Look for the detail panel/sidebar
        const text = document.body.innerText;
        const offerIdMatch = text.match(/Offer[:\s]+([A-Z0-9]{8,})/i);
        return {
          offerId: offerIdMatch ? offerIdMatch[1] : null,
        };
      });

      if (detail.offerId && offers[i]) {
        offers[i].offerId = detail.offerId;
      }

      // Close the panel
      const closeBtn = page.locator('button[aria-label="Close"], [class*="close"] button').first();
      if (await closeBtn.isVisible().catch(() => false)) {
        await closeBtn.click();
        await page.waitForTimeout(500);
      }
    } catch (e) {
      // Skip offers we can't click
    }
  }

  console.log(`  Found ${offers.length} offers`);
  return offers;
}

// ── Great Gift ──────────────────────────────────────────────────────────────
async function scrapeGreatGift(page, gmailToken) {
  console.log('🎄 Scraping Great Gift...');

  // Navigate through: caesars.com promotions > Great Gift Wrap Up > Shop Now
  // This triggers 2FA on the external site
  try {
    await page.goto('https://www.caesars.com/rewards/offers', {
      waitUntil: 'networkidle', timeout: 30000
    });
    await page.waitForTimeout(2000);

    // Look for Great Gift link
    const giftLink = page.locator('a:has-text("Great Gift"), a:has-text("Gift Wrap")').first();
    if (await giftLink.isVisible().catch(() => false)) {
      await giftLink.click();
      await page.waitForTimeout(3000);

      // Look for Shop Now
      const shopLink = page.locator('a:has-text("Shop Now")').first();
      if (await shopLink.isVisible().catch(() => false)) {
        await shopLink.click();
        await page.waitForTimeout(5000);
      }
    }

    // Handle 2FA if triggered
    await handle2FA(page, gmailToken);

    // Extract points from the Great Gift page
    await page.waitForTimeout(3000);
    const points = await page.evaluate(() => {
      const text = document.body.innerText;
      const match = text.match(/Great Gift Points Balance:\s*([\d,]+)/i);
      return match ? parseInt(match[1].replace(/,/g, '')) : null;
    });

    console.log(`  Great Gift Points: ${points}`);
    return points;
  } catch (err) {
    console.log(`  ⚠️ Could not scrape Great Gift: ${err.message}`);
    return null;
  }
}

// ── Save Functions ──────────────────────────────────────────────────────────
async function saveSnapshot(data) {
  const { error } = await supabase.from('caesars_rewards_snapshots').insert({
    reward_credits: data.rewardCredits,
    tier_credits: data.tierCredits,
    tier_status: data.tierStatus,
    tier_next: data.tierNext,
    tier_credits_needed: data.tierCreditsNeeded,
    last_earned_date: parseDate(data.lastEarnedDate),
    credits_expire_date: parseDate(data.creditsExpireDate),
    great_gift_points: data.greatGiftPoints,
  });
  if (error) console.error('  ❌ Snapshot save error:', error.message);
  else console.log('  💾 Saved rewards snapshot');
}

async function saveReservations(reservations) {
  for (const r of reservations) {
    const { error } = await supabase.from('caesars_reservations').upsert({
      confirmation_code: r.confirmationCode,
      property: r.property,
      location: r.location,
      check_in: parseDate(r.checkIn),
      check_out: parseDate(r.checkOut),
      adults: r.adults,
      children: r.children,
      status: 'Active',
      tab: r.tab,
      updated_at: new Date().toISOString(),
    }, { onConflict: 'confirmation_code' });
    if (error) console.error(`  ❌ Reservation ${r.confirmationCode}:`, error.message);
  }
  console.log(`  💾 Saved ${reservations.length} reservations`);
}

async function saveOffers(offers) {
  let saved = 0;
  for (const o of offers) {
    if (!o.offerId) continue;

    // Parse dates from "Valid MM.DD.YY - MM.DD.YY" format
    let validStart = null, validEnd = null;
    if (o.dates) {
      const datesMatch = o.dates.match(/(\d{2}[.\/-]\d{2}[.\/-]\d{2,4})\s*[-–]\s*(\d{2}[.\/-]\d{2}[.\/-]\d{2,4})/);
      if (datesMatch) {
        validStart = parseDate(datesMatch[1]);
        validEnd = parseDate(datesMatch[2]);
      }
    }

    const { error } = await supabase.from('caesars_offers').upsert({
      offer_id: o.offerId,
      title: o.title,
      section: o.section,
      eligible_properties: o.property,
      valid_start: validStart,
      valid_end: validEnd,
      last_seen: new Date().toISOString(),
    }, { onConflict: 'offer_id' });
    if (!error) saved++;
  }
  console.log(`  💾 Saved ${saved} offers`);
}

module.exports = { scrapeCaesars };
