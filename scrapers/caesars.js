const { supabase, parseDate } = require('./db');
const { fetch2FACode } = require('./gmail');
const { createPage, humanType, humanClick, humanNavigate, randomDelay, humanScroll } = require('./browser');

async function scrapeCaesars(browser) {
  console.log('\n═══════════════════════════════════════');
  console.log('  CAESARS REWARDS SCRAPER');
  console.log('═══════════════════════════════════════\n');

  const page = await createPage(browser);

  try {
    await login(page);
    await handle2FA(page);

    const rewards = await scrapeRewardsHome(page);
    const pastRes = await scrapeReservations(page, 'past');
    const currentRes = await scrapeReservations(page, 'current');
    const offers = await scrapeOffers(page);
    const greatGift = await scrapeGreatGift(page);
    rewards.greatGiftPoints = greatGift;

    await saveSnapshot(rewards);
    await saveReservations([...pastRes, ...currentRes]);
    await saveOffers(offers);

    console.log('\n✅ Caesars scrape complete!\n');
  } catch (err) {
    console.error('❌ Caesars error:', err.message);
  } finally {
    await page.close();
  }
}

// ── Login ───────────────────────────────────────────────────────────────────
async function login(page) {
  console.log('🔑 Logging in...');
  await humanNavigate(page, 'https://www.caesars.com/myrewards/profile/signin/');

  // Wait for the form to render (React SPA)
  await page.waitForSelector('input', { timeout: 20000 });
  await randomDelay(2000, 4000);

  // Find and fill username — try multiple selectors
  const usernameSelectors = [
    'input[type="text"]',
    'input[type="email"]',
    'input[name="email"]',
    'input[name="username"]',
    'input:not([type="password"]):not([type="hidden"])',
  ];

  let filled = false;
  for (const sel of usernameSelectors) {
    try {
      const el = await page.$(sel);
      if (el) {
        const isVisible = await page.evaluate(e => {
          const rect = e.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        }, el);
        if (isVisible) {
          await humanType(page, sel, process.env.CAESARS_USERNAME);
          filled = true;
          break;
        }
      }
    } catch (e) { continue; }
  }

  if (!filled) throw new Error('Could not find username input');

  await randomDelay(500, 1200);

  // Fill password
  await humanType(page, 'input[type="password"]', process.env.CAESARS_PASSWORD);
  await randomDelay(800, 1800);

  // Click sign in
  const signInSelectors = [
    'button[type="submit"]',
    'button:nth-of-type(1)',
  ];

  for (const sel of signInSelectors) {
    try {
      const btn = await page.$(sel);
      if (btn) {
        const text = await page.evaluate(e => e.textContent, btn);
        if (text && (text.includes('SIGN IN') || text.includes('Sign In') || text.includes('Log In'))) {
          await humanClick(page, sel);
          break;
        }
      }
    } catch (e) { continue; }
  }

  // Fallback: click any submit button
  try {
    await humanClick(page, 'button[type="submit"]');
  } catch (e) {}

  await randomDelay(4000, 7000);
  console.log('  URL after login:', page.url());
}

// ── 2FA ─────────────────────────────────────────────────────────────────────
async function handle2FA(page) {
  if (!page.url().includes('/verification/step-up')) return;

  console.log('🔐 2FA required...');
  await page.waitForSelector('input[maxlength="1"]', { timeout: 10000 });
  await randomDelay(8000, 12000); // Wait for email to arrive

  const code = await fetch2FACode();
  if (!code) throw new Error('Could not get 2FA code');

  console.log(`  Entering code: ${code}`);
  const inputs = await page.$$('input[maxlength="1"]');

  for (let i = 0; i < Math.min(code.length, inputs.length); i++) {
    await inputs[i].click();
    await randomDelay(100, 300);
    await page.keyboard.type(code[i], { delay: Math.floor(Math.random() * 100) + 80 });
    await randomDelay(200, 500);
  }

  await randomDelay(2000, 4000);

  // Wait for redirect
  try {
    await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 });
  } catch (e) {}

  await randomDelay(2000, 3000);
  console.log('  ✅ 2FA complete');
}

// ── Rewards Home ────────────────────────────────────────────────────────────
async function scrapeRewardsHome(page) {
  console.log('📊 Scraping rewards home...');
  await humanNavigate(page, 'https://www.caesars.com/rewards/home');
  await randomDelay(2000, 4000);

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
  await humanNavigate(page, 'https://www.caesars.com/rewards/stays');

  // Click tab
  try {
    const tabText = tab.toUpperCase();
    await page.evaluate((t) => {
      const links = [...document.querySelectorAll('a, button, span')];
      const el = links.find(l => l.textContent.trim() === t);
      if (el) el.click();
    }, tabText);
    await randomDelay(2000, 3000);
  } catch (e) {}

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
  await humanNavigate(page, 'https://www.caesars.com/rewards/offers');
  await randomDelay(2000, 4000);

  // Clear filters
  try {
    const clearBtn = await page.$('button, a');
    const buttons = await page.$$('button, a');
    for (const btn of buttons) {
      const text = await page.evaluate(e => e.textContent.trim(), btn);
      if (text === 'Clear Filters') {
        await btn.click();
        await randomDelay(2000, 3000);
        break;
      }
    }
  } catch (e) {}

  // Click "See More" buttons to expand sections
  for (let i = 0; i < 10; i++) {
    try {
      const buttons = await page.$$('button, a');
      let found = false;
      for (const btn of buttons) {
        const text = await page.evaluate(e => e.textContent.trim(), btn);
        if (text.includes('See More')) {
          await btn.click();
          await randomDelay(1500, 3000);
          await humanScroll(page, 300);
          found = true;
          break;
        }
      }
      if (!found) break;
    } catch (e) { break; }
  }

  // Extract offer data from page
  const offers = await page.evaluate(() => {
    const results = [];
    const text = document.body.innerText;
    let currentSection = 'Unknown';
    const lines = text.split('\n').map(l => l.trim()).filter(Boolean);

    for (let i = 0; i < lines.length; i++) {
      const sectionMatch = lines[i].match(/^(EXPIRING.*?|(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+OFFERS?)\s*\((\d+)\)/i);
      if (sectionMatch) {
        currentSection = sectionMatch[1].trim();
        continue;
      }

      const expiresMatch = lines[i].match(/^Expires?\s+(today|tomorrow|\d.+)/i);
      const validMatch = lines[i].match(/^Valid\s+(\d.+)/i);

      if (expiresMatch || validMatch) {
        let title = '';
        for (let j = i - 1; j >= Math.max(0, i - 4); j--) {
          if (lines[j].match(/^(EXPIRING|JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)/i)) break;
          if (lines[j].match(/^(See More|Clear Filters|FILTER|DESTINATIONS|DATES|OFFER TYPE)/i)) break;
          title = lines[j];
          break;
        }

        let property = '';
        if (i >= 2 && !lines[i - 1].match(/^[A-Z\$\d]/) && lines[i - 1] !== title) {
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

  console.log(`  Found ${offers.length} offers`);
  return offers;
}

// ── Great Gift ──────────────────────────────────────────────────────────────
async function scrapeGreatGift(page) {
  console.log('🎄 Scraping Great Gift...');
  try {
    // Navigate to promotions and find Great Gift link
    await humanNavigate(page, 'https://www.caesars.com/rewards/offers');
    await randomDelay(2000, 3000);

    // Look for Great Gift / Gift Wrap link
    const links = await page.$$('a');
    for (const link of links) {
      const text = await page.evaluate(e => e.textContent, link);
      if (text && (text.includes('Great Gift') || text.includes('Gift Wrap'))) {
        await link.click();
        await randomDelay(3000, 5000);

        // Look for Shop Now
        const shopLinks = await page.$$('a');
        for (const sl of shopLinks) {
          const st = await page.evaluate(e => e.textContent, sl);
          if (st && st.includes('Shop Now')) {
            await sl.click();
            await randomDelay(5000, 8000);
            break;
          }
        }
        break;
      }
    }

    // Handle 2FA if triggered
    if (page.url().includes('/verification/step-up')) {
      await handle2FA(page);
    }

    await randomDelay(3000, 5000);

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
    const offerId = o.offerId || `${o.title}-${o.dates}`.replace(/\s+/g, '-').substring(0, 50);
    if (!offerId || !o.title) continue;

    const { error } = await supabase.from('caesars_offers').upsert({
      offer_id: offerId,
      title: o.title,
      section: o.section,
      eligible_properties: o.property,
      last_seen: new Date().toISOString(),
    }, { onConflict: 'offer_id' });
    if (!error) saved++;
  }
  console.log(`  💾 Saved ${saved} offers`);
}

module.exports = { scrapeCaesars };
