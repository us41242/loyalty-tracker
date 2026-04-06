const { supabase, parseDate } = require('./db');

async function scrapeMGM(browser) {
  console.log('\n═══════════════════════════════════════');
  console.log('  MGM REWARDS SCRAPER');
  console.log('═══════════════════════════════════════\n');

  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });
  const page = await context.newPage();

  try {
    // 1. Login
    await login(page);

    // 2. Scrape rewards
    const rewards = await scrapeRewards(page);

    // 3. Scrape trips
    const trips = await scrapeTrips(page);

    // 4. Save to Supabase
    await saveSnapshot(rewards);
    await saveTrips(trips);

    console.log('\n✅ MGM scrape complete!\n');
  } catch (err) {
    console.error('❌ MGM error:', err.message);
  } finally {
    await context.close();
  }
}

// ── Login ───────────────────────────────────────────────────────────────────
async function login(page) {
  console.log('🔑 Logging in to MGM...');
  await page.goto('https://www.mgmresorts.com/identity/?client_id=mgm_app_web&redirect_uri=https://www.mgmresorts.com/rewards/&scopes=', {
    waitUntil: 'networkidle', timeout: 30000
  });
  await page.waitForTimeout(3000);

  // Fill email
  const emailInput = page.locator('input[type="email"], input[name="email"], input[id*="email"]').first();
  if (await emailInput.isVisible().catch(() => false)) {
    await emailInput.fill(process.env.MGM_EMAIL);
    await page.waitForTimeout(500);

    // Some MGM flows have a "Next" button before password
    const nextBtn = page.locator('button:has-text("Next"), button:has-text("Continue")').first();
    if (await nextBtn.isVisible().catch(() => false)) {
      await nextBtn.click();
      await page.waitForTimeout(2000);
    }

    // Fill password
    const passInput = page.locator('input[type="password"]').first();
    await passInput.waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
    if (await passInput.isVisible().catch(() => false)) {
      await passInput.fill(process.env.MGM_PASSWORD);
      await page.waitForTimeout(500);

      const submitBtn = page.locator('button[type="submit"], button:has-text("Sign In"), button:has-text("Log In")').first();
      await submitBtn.click();
    }

    await page.waitForTimeout(5000);
  }

  console.log('  URL after login:', page.url());
}

// ── Rewards ─────────────────────────────────────────────────────────────────
async function scrapeRewards(page) {
  console.log('📊 Scraping MGM rewards...');

  if (!page.url().includes('/rewards')) {
    await page.goto('https://www.mgmresorts.com/rewards/', {
      waitUntil: 'networkidle', timeout: 30000
    });
  }
  await page.waitForTimeout(3000);

  const data = await page.evaluate(() => {
    const text = document.body.innerText;

    // Tier status
    const tierMatch = text.match(/(Sapphire|Pearl|Gold|Platinum|Noir)/i);

    // Tier credits
    const tcMatch = text.match(/([\d,]+)\s*Tier Credits/i);

    // To next tier
    const toNextMatch = text.match(/([\d,]+)\s*to advance to\s+(\w+)/i);

    // MGM Rewards Points
    const pointsMatch = text.match(/MGM Rewards Points\s*([\d,]+)/i) ||
                        text.match(/([\d,]+)\s*\$[\d.]+\s*in comps/i);
    const compsMatch = text.match(/\$([\d.]+)\s*in comps/i);

    // FreePlay
    const freeplayMatch = text.match(/FREEPLAY[®]?\s*\$([\d.]+)/i);

    // Slot Dollars
    const slotMatch = text.match(/SLOT DOLLARS[®]?\s*\$([\d.]+)/i);

    // Holiday Gift Points
    const giftMatch = text.match(/Holiday Gift Points\s*([\d,.]+)/i);

    // Milestone Rewards
    const milestoneMatch = text.match(/(\d+)\s*Milestone Rewards/i);

    return {
      tierStatus: tierMatch ? tierMatch[1] : null,
      tierCredits: tcMatch ? parseInt(tcMatch[1].replace(/,/g, '')) : null,
      tierCreditsToNext: toNextMatch ? parseInt(toNextMatch[1].replace(/,/g, '')) : null,
      tierNext: toNextMatch ? toNextMatch[2] : null,
      rewardsPoints: pointsMatch ? parseInt(pointsMatch[1].replace(/,/g, '')) : null,
      rewardsCompsValue: compsMatch ? parseFloat(compsMatch[1]) : null,
      freeplay: freeplayMatch ? parseFloat(freeplayMatch[1]) : null,
      slotDollars: slotMatch ? parseFloat(slotMatch[1]) : null,
      holidayGiftPoints: giftMatch ? parseFloat(giftMatch[1].replace(/,/g, '')) : null,
      milestoneRewards: milestoneMatch ? parseInt(milestoneMatch[1]) : null,
    };
  });

  console.log(`  Tier: ${data.tierStatus} | Credits: ${data.tierCredits} | Points: ${data.rewardsPoints}`);
  return data;
}

// ── Trips ───────────────────────────────────────────────────────────────────
async function scrapeTrips(page) {
  console.log('📋 Scraping MGM trips...');
  await page.goto('https://www.mgmresorts.com/trips/', {
    waitUntil: 'networkidle', timeout: 30000
  });
  await page.waitForTimeout(3000);

  const trips = [];

  // Check both upcoming and past tabs
  for (const tab of ['Upcoming', 'Past']) {
    const tabBtn = page.locator(`text=${tab}`).first();
    if (await tabBtn.isVisible().catch(() => false)) {
      await tabBtn.click();
      await page.waitForTimeout(2000);

      const tabTrips = await page.evaluate((currentTab) => {
        const text = document.body.innerText;
        // If there are no trips, it shows "Make some new memories"
        if (text.includes('Make some new memories')) return [];

        const results = [];
        // Parse trip cards — structure TBD based on actual data
        // MGM trips show: property, dates, confirmation code
        const lines = text.split('\n').map(l => l.trim()).filter(Boolean);

        for (let i = 0; i < lines.length; i++) {
          const confMatch = lines[i].match(/Confirmation[:\s#]+([A-Z0-9]+)/i);
          if (confMatch) {
            results.push({
              confirmationCode: confMatch[1],
              tab: currentTab.toLowerCase(),
            });
          }
        }

        return results;
      }, tab);

      trips.push(...tabTrips);
    }
  }

  console.log(`  Found ${trips.length} trips`);
  return trips;
}

// ── Save Functions ──────────────────────────────────────────────────────────
async function saveSnapshot(data) {
  const { error } = await supabase.from('mgm_rewards_snapshots').insert({
    tier_status: data.tierStatus,
    tier_credits: data.tierCredits,
    tier_credits_to_next: data.tierCreditsToNext,
    tier_next: data.tierNext,
    rewards_points: data.rewardsPoints,
    rewards_comps_value: data.rewardsCompsValue,
    freeplay: data.freeplay,
    slot_dollars: data.slotDollars,
    holiday_gift_points: data.holidayGiftPoints,
    milestone_rewards: data.milestoneRewards,
  });
  if (error) console.error('  ❌ Snapshot save error:', error.message);
  else console.log('  💾 Saved MGM snapshot');
}

async function saveTrips(trips) {
  for (const t of trips) {
    if (!t.confirmationCode) continue;
    const { error } = await supabase.from('mgm_trips').upsert({
      confirmation_code: t.confirmationCode,
      property: t.property || null,
      check_in: t.checkIn ? parseDate(t.checkIn) : null,
      check_out: t.checkOut ? parseDate(t.checkOut) : null,
      status: t.status || 'Active',
      tab: t.tab,
      updated_at: new Date().toISOString(),
    }, { onConflict: 'confirmation_code' });
    if (error) console.error(`  ❌ Trip ${t.confirmationCode}:`, error.message);
  }
  if (trips.length > 0) console.log(`  💾 Saved ${trips.length} trips`);
}

module.exports = { scrapeMGM };
