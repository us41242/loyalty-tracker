const { supabase, parseDate } = require('./db');

async function scrapeRio(browser) {
  console.log('\n═══════════════════════════════════════');
  console.log('  RIO REWARDS SCRAPER');
  console.log('═══════════════════════════════════════\n');

  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });
  const page = await context.newPage();

  try {
    // 1. Login
    await login(page);

    // 2. Scrape rewards + offers (same page)
    const data = await scrapeRewardsAndOffers(page);

    // 3. Save to Supabase
    await saveSnapshot(data.snapshot);
    await saveOffers(data.offers);

    console.log('\n✅ Rio scrape complete!\n');
  } catch (err) {
    console.error('❌ Rio error:', err.message);
  } finally {
    await context.close();
  }
}

// ── Login ───────────────────────────────────────────────────────────────────
async function login(page) {
  console.log('🔑 Logging in to Rio...');
  await page.goto('https://www.riolasvegas.com/api/auth/login?returnTo=/rio-rewards/offers', {
    waitUntil: 'networkidle', timeout: 30000
  });
  await page.waitForTimeout(3000);

  // Rio uses Auth0 — fill email and password
  const emailInput = page.locator('input[name="email"], input[type="email"], input[name="username"]').first();
  if (await emailInput.isVisible().catch(() => false)) {
    await emailInput.fill(process.env.RIO_USERNAME);

    const passInput = page.locator('input[type="password"]').first();
    await passInput.fill(process.env.RIO_PASSWORD);
    await page.waitForTimeout(500);

    const submitBtn = page.locator('button[type="submit"]').first();
    await submitBtn.click();

    await page.waitForTimeout(5000);
  }

  console.log('  URL after login:', page.url());
}

// ── Scrape Rewards + Offers ─────────────────────────────────────────────────
async function scrapeRewardsAndOffers(page) {
  // Navigate to offers page (has account summary at top)
  if (!page.url().includes('/rio-rewards/offers')) {
    await page.goto('https://www.riolasvegas.com/rio-rewards/offers', {
      waitUntil: 'networkidle', timeout: 30000
    });
  }
  await page.waitForTimeout(3000);

  console.log('📊 Scraping Rio rewards and offers...');

  const data = await page.evaluate(() => {
    const text = document.body.innerText;

    // Account summary
    const tierMatch = text.match(/(ROUGE|AZUL|GOLD|PLATINUM)\s+MEMBER\s*\|\s*#(\d+)/i);
    const pointsMatch = text.match(/([\d,]+)\s*RIO REWARDS POINTS/i);
    const creditMatch = text.match(/\$([\d,.]+)\s*RESORT CREDIT/i);
    const earnedMatch = text.match(/([\d,]+)\s*POINTS EARNED IN \d{4}/i);
    const toNextMatch = text.match(/([\d,]+)\s*POINTS TO (\w+)/i);
    const validMatch = text.match(/earned (?:.*?) status through\s+([\w\s]+\d{4})/i);

    const snapshot = {
      tierStatus: tierMatch ? tierMatch[1] : null,
      memberNumber: tierMatch ? tierMatch[2] : null,
      pointsBalance: pointsMatch ? parseInt(pointsMatch[1].replace(/,/g, '')) : null,
      resortCredit: creditMatch ? parseFloat(creditMatch[1]) : null,
      pointsEarnedYear: earnedMatch ? parseInt(earnedMatch[1].replace(/,/g, '')) : null,
      pointsToNextTier: toNextMatch ? parseInt(toNextMatch[1].replace(/,/g, '')) : null,
      nextTier: toNextMatch ? toNextMatch[2] : null,
      statusValidThrough: validMatch ? validMatch[1].trim() : null,
    };

    // Offers
    const offers = [];
    // Look for offer cards — they have titles, dates, and descriptions
    const offerElements = document.querySelectorAll('[class*="offer"], [class*="Offer"], article');

    if (offerElements.length === 0) {
      // Fallback: parse from text
      const offerSection = text.split(/Your Offers/i)[1] || '';
      const offerBlocks = offerSection.split(/(?=(?:Your |Free |\$\d))/);

      for (const block of offerBlocks) {
        const titleMatch = block.match(/^(.+?)(?:\n|$)/);
        const dateMatch = block.match(/(?:Offer Valid|Book Offer|Stay Dates):\s*(.+?)(?:\n|$)/i);
        const descMatch = block.match(/\$\d+.+?(?:\n|$)/);

        if (titleMatch && titleMatch[1].trim().length > 3) {
          offers.push({
            title: titleMatch[1].trim(),
            dates: dateMatch ? dateMatch[1].trim() : null,
            description: descMatch ? descMatch[0].trim() : null,
          });
        }
      }
    }

    return { snapshot, offers };
  });

  console.log(`  Tier: ${data.snapshot.tierStatus} | Points: ${data.snapshot.pointsBalance}`);
  console.log(`  Found ${data.offers.length} offers`);
  return data;
}

// ── Save Functions ──────────────────────────────────────────────────────────
async function saveSnapshot(data) {
  const { error } = await supabase.from('rio_rewards_snapshots').insert({
    tier_status: data.tierStatus,
    member_number: data.memberNumber,
    points_balance: data.pointsBalance,
    resort_credit: data.resortCredit,
    points_earned_year: data.pointsEarnedYear,
    points_to_next_tier: data.pointsToNextTier,
    next_tier: data.nextTier,
    status_valid_through: parseDate(data.statusValidThrough),
  });
  if (error) console.error('  ❌ Snapshot save error:', error.message);
  else console.log('  💾 Saved Rio snapshot');
}

async function saveOffers(offers) {
  let saved = 0;
  for (const o of offers) {
    if (!o.title) continue;

    let validStart = null, validEnd = null;
    if (o.dates) {
      const parts = o.dates.split(/\s*[-–]\s*/);
      if (parts.length === 2) {
        // "Now" means today
        validStart = parts[0].trim() === 'Now' ? new Date().toISOString().split('T')[0] : parseDate(parts[0].trim());
        validEnd = parseDate(parts[1].trim());
      }
    }

    const { error } = await supabase.from('rio_offers').upsert({
      title: o.title,
      description: o.description,
      valid_start: validStart,
      valid_end: validEnd,
      last_seen: new Date().toISOString(),
    }, { onConflict: 'title,valid_start,valid_end' });
    if (!error) saved++;
  }
  console.log(`  💾 Saved ${saved} offers`);
}

module.exports = { scrapeRio };
