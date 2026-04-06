const { supabase, parseDate } = require('./db');
const { fetch2FACode } = require('./gmail');
const { createPage, humanType, reactType, humanClick, humanNavigate, randomDelay, humanScroll } = require('./browser');
const fs = require('fs');
const path = require('path');

// Debug: save screenshot and HTML for troubleshooting
async function debugPage(page, label) {
  try {
    const dir = path.join(process.cwd(), 'debug');
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

    await page.screenshot({ path: path.join(dir, `${label}.png`), fullPage: true });
    const html = await page.content();
    fs.writeFileSync(path.join(dir, `${label}.html`), html);
    console.log(`  📸 Debug saved: debug/${label}.png + .html`);
  } catch (e) {
    console.log(`  ⚠️ Could not save debug for ${label}: ${e.message}`);
  }
}

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
    await debugPage(page, 'caesars-error');
  } finally {
    await page.close();
  }
}

// ── Login ───────────────────────────────────────────────────────────────────
async function login(page) {
  console.log('🔑 Logging in...');
  await humanNavigate(page, 'https://www.caesars.com/myrewards/profile/signin/');

  // Wait for the React SPA to render
  await randomDelay(3000, 5000);

  // Debug: dump what inputs are on the page
  const inputInfo = await page.evaluate(() => {
    const inputs = [...document.querySelectorAll('input')];
    return inputs.map(i => ({
      type: i.type,
      name: i.name,
      id: i.id,
      placeholder: i.placeholder,
      className: i.className.substring(0, 50),
      visible: i.offsetWidth > 0 && i.offsetHeight > 0,
      ariaLabel: i.getAttribute('aria-label'),
    }));
  });
  console.log('  Inputs found:', JSON.stringify(inputInfo, null, 2));

  // Debug: dump all buttons
  const buttonInfo = await page.evaluate(() => {
    const buttons = [...document.querySelectorAll('button')];
    return buttons.map(b => ({
      text: b.textContent.trim().substring(0, 40),
      type: b.type,
      className: b.className.substring(0, 50),
      visible: b.offsetWidth > 0 && b.offsetHeight > 0,
    }));
  });
  console.log('  Buttons found:', JSON.stringify(buttonInfo, null, 2));

  await debugPage(page, 'caesars-login-page');

  if (inputInfo.length === 0) {
    console.log('  ⚠️ No inputs found — page may not have rendered');
    // Try waiting longer
    await randomDelay(5000, 8000);

    const retryInputs = await page.evaluate(() => {
      return [...document.querySelectorAll('input')].map(i => ({
        type: i.type, name: i.name, placeholder: i.placeholder,
        visible: i.offsetWidth > 0 && i.offsetHeight > 0,
      }));
    });
    console.log('  Retry inputs:', JSON.stringify(retryInputs));

    if (retryInputs.length === 0) {
      // Check if there's a Cloudflare/bot challenge
      const bodyText = await page.evaluate(() => document.body.innerText.substring(0, 500));
      console.log('  Page text:', bodyText);
      throw new Error('Login page did not render — possible bot detection');
    }
  }

  // Find username input — try by aria-label, placeholder, type, etc.
  let usernameSelector = null;
  for (const input of inputInfo) {
    if (!input.visible) continue;
    if (input.type === 'password') continue;
    if (input.type === 'hidden') continue;

    // Build a selector for this input
    if (input.id) usernameSelector = `#${input.id}`;
    else if (input.name) usernameSelector = `input[name="${input.name}"]`;
    else if (input.ariaLabel) usernameSelector = `input[aria-label="${input.ariaLabel}"]`;
    else if (input.placeholder) usernameSelector = `input[placeholder="${input.placeholder}"]`;
    else usernameSelector = `input[type="${input.type || 'text'}"]`;
    break;
  }

  if (!usernameSelector) throw new Error('Could not find username input');
  console.log(`  Using username selector: ${usernameSelector}`);

  // Use React-compatible typing for Caesars (React SPA)
  await reactType(page, usernameSelector, process.env.CAESARS_USERNAME);
  console.log('  Username entered');
  await randomDelay(800, 1500);

  // Password — also React controlled
  const passSelector = 'input[type="password"]';
  await reactType(page, passSelector, process.env.CAESARS_PASSWORD);
  console.log('  Password entered');
  await randomDelay(1000, 2000);

  // Find sign in button
  const signInClicked = await page.evaluate(() => {
    const buttons = [...document.querySelectorAll('button')];
    const signIn = buttons.find(b =>
      b.textContent.trim().match(/^(SIGN IN|Sign In|Log In|LOGIN)$/i) &&
      b.offsetWidth > 0
    );
    if (signIn) {
      signIn.click();
      return true;
    }
    return false;
  });

  if (!signInClicked) {
    console.log('  ⚠️ Could not find sign-in button, trying submit');
    await page.keyboard.press('Enter');
  }

  console.log('  Waiting for login response...');
  await randomDelay(5000, 8000);

  // Check if we navigated away from signin
  const currentUrl = page.url();
  console.log('  URL after login:', currentUrl);

  if (currentUrl.includes('/signin')) {
    await debugPage(page, 'caesars-login-failed');

    // Check for error messages
    const errorText = await page.evaluate(() => {
      const errors = document.querySelectorAll('[class*="error"], [class*="alert"], [role="alert"]');
      return [...errors].map(e => e.textContent.trim()).join(' | ');
    });
    if (errorText) console.log('  Error messages:', errorText);

    console.log('  ⚠️ Login may have failed — continuing anyway');
  }
}

// ── 2FA ─────────────────────────────────────────────────────────────────────
async function handle2FA(page) {
  if (!page.url().includes('/verification/step-up')) return;

  console.log('🔐 2FA required...');
  await page.waitForSelector('input[maxlength="1"]', { timeout: 10000 });
  await randomDelay(8000, 12000);

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

  if (!data.rewardCredits) {
    await debugPage(page, 'caesars-rewards-home');
  }

  console.log('  Credits:', data.rewardCredits, '| Tier:', data.tierCredits, data.tierStatus);
  return data;
}

// ── Reservations ────────────────────────────────────────────────────────────
async function scrapeReservations(page, tab) {
  console.log(`📋 Scraping ${tab} reservations...`);
  await humanNavigate(page, 'https://www.caesars.com/rewards/stays');

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
    const cleared = await page.evaluate(() => {
      const els = [...document.querySelectorAll('button, a')];
      const btn = els.find(e => e.textContent.trim() === 'Clear Filters');
      if (btn) { btn.click(); return true; }
      return false;
    });
    if (cleared) await randomDelay(2000, 3000);
  } catch (e) {}

  // Click "See More" buttons
  for (let i = 0; i < 10; i++) {
    const clicked = await page.evaluate(() => {
      const els = [...document.querySelectorAll('button, a')];
      const btn = els.find(e => e.textContent.trim().includes('See More'));
      if (btn) { btn.click(); return true; }
      return false;
    });
    if (!clicked) break;
    await randomDelay(1500, 3000);
    await humanScroll(page, 300);
  }

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
    await humanNavigate(page, 'https://www.caesars.com/rewards/offers');
    await randomDelay(2000, 3000);

    const clicked = await page.evaluate(() => {
      const links = [...document.querySelectorAll('a')];
      const giftLink = links.find(l => l.textContent.includes('Great Gift') || l.textContent.includes('Gift Wrap'));
      if (giftLink) { giftLink.click(); return true; }
      return false;
    });

    if (clicked) {
      await randomDelay(3000, 5000);
      const shopClicked = await page.evaluate(() => {
        const links = [...document.querySelectorAll('a')];
        const shop = links.find(l => l.textContent.includes('Shop Now'));
        if (shop) { shop.click(); return true; }
        return false;
      });
      if (shopClicked) await randomDelay(5000, 8000);
    }

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
