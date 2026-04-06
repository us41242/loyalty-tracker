const { supabase, parseDate } = require('./db');
const { createPage, humanType, reactType, humanClick, humanNavigate, randomDelay, humanScroll } = require('./browser');
const fs = require('fs');
const path = require('path');

async function debugPage(page, label) {
  try {
    const dir = path.join(process.cwd(), 'debug');
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    await page.screenshot({ path: path.join(dir, `${label}.png`), fullPage: true });
    const html = await page.content();
    fs.writeFileSync(path.join(dir, `${label}.html`), html);
    console.log(`  📸 Debug saved: debug/${label}.png + .html`);
  } catch (e) {}
}

async function scrapeMGM(browser) {
  console.log('\n═══════════════════════════════════════');
  console.log('  MGM REWARDS SCRAPER');
  console.log('═══════════════════════════════════════\n');

  const page = await createPage(browser);

  try {
    await login(page);
    const rewards = await scrapeRewards(page);
    const trips = await scrapeTrips(page);
    await saveSnapshot(rewards);
    await saveTrips(trips);
    console.log('\n✅ MGM scrape complete!\n');
  } catch (err) {
    console.error('❌ MGM error:', err.message);
  } finally {
    await page.close();
  }
}

async function login(page) {
  console.log('🔑 Logging in to MGM...');

  // Navigate to main site first (build cookies), then go to login
  await humanNavigate(page, 'https://www.mgmresorts.com/');
  await randomDelay(2000, 4000);
  await humanScroll(page, 300);
  await randomDelay(1000, 2000);

  // Now navigate to login
  await humanNavigate(page, 'https://www.mgmresorts.com/identity/?client_id=mgm_app_web&redirect_uri=https://www.mgmresorts.com/rewards/&scopes=');
  await randomDelay(3000, 6000);

  await debugPage(page, 'mgm-login-page');

  // Debug
  const pageText = await page.evaluate(() => document.body.innerText.substring(0, 300));
  console.log('  Page text:', pageText.replace(/\n/g, ' ').substring(0, 200));

  // Check for error on page load
  if (pageText.includes('error') || pageText.includes('Oops')) {
    console.log('  ⚠️ MGM showing error on load — may be detecting bot');
    // Try refreshing with more delay
    await randomDelay(3000, 5000);
    await page.reload({ waitUntil: 'networkidle2' });
    await randomDelay(3000, 5000);
  }

  try {
    // Step 1: Enter email
    const emailInput = await page.$('#email');
    if (emailInput) {
      console.log('  Found email input');
      // Click, wait, then type slowly
      await page.click('#email');
      await randomDelay(500, 1000);

      // Type email character by character with human delays
      for (const char of process.env.MGM_EMAIL) {
        await page.keyboard.type(char, { delay: Math.floor(Math.random() * 120) + 80 });
      }
      await randomDelay(1000, 2000);

      // Click Next button with mouse movement
      const nextBtn = await page.$('button[type="submit"]');
      if (nextBtn) {
        const box = await nextBtn.boundingBox();
        if (box) {
          await page.mouse.move(box.x + box.width/2, box.y + box.height/2, { steps: 15 });
          await randomDelay(200, 500);
          await page.mouse.click(box.x + box.width/2, box.y + box.height/2);
        }
      }
      console.log('  Clicked Next');
      await randomDelay(3000, 5000);

      await debugPage(page, 'mgm-after-next');

      // Step 2: Enter password (if password field appears)
      const passInput = await page.$('input[type="password"]');
      if (passInput) {
        console.log('  Found password input');
        await page.click('input[type="password"]');
        await randomDelay(500, 1000);

        for (const char of process.env.MGM_PASSWORD) {
          await page.keyboard.type(char, { delay: Math.floor(Math.random() * 120) + 80 });
        }
        await randomDelay(1000, 2000);

        // Click Sign In
        const signInBtn = await page.$('button[type="submit"]');
        if (signInBtn) {
          const box = await signInBtn.boundingBox();
          if (box) {
            await page.mouse.move(box.x + box.width/2, box.y + box.height/2, { steps: 15 });
            await randomDelay(200, 500);
            await page.mouse.click(box.x + box.width/2, box.y + box.height/2);
          }
        }
        console.log('  Clicked Sign In');
        await randomDelay(5000, 8000);
      } else {
        console.log('  ⚠️ No password field found after Next');
        await debugPage(page, 'mgm-no-password');
      }
    } else {
      console.log('  ⚠️ No email input found');
    }
  } catch (e) {
    console.log('  Login flow issue:', e.message);
  }

  const currentUrl = page.url();
  console.log('  URL after login:', currentUrl);

  if (currentUrl.includes('/identity')) {
    await debugPage(page, 'mgm-login-failed');
  }
}

async function scrapeRewards(page) {
  console.log('📊 Scraping MGM rewards...');

  if (!page.url().includes('/rewards')) {
    await humanNavigate(page, 'https://www.mgmresorts.com/rewards/');
  }
  await randomDelay(2000, 4000);

  const data = await page.evaluate(() => {
    const text = document.body.innerText;

    const tierMatch = text.match(/(Sapphire|Pearl|Gold|Platinum|Noir)/i);
    const tcMatch = text.match(/([\d,]+)\s*Tier Credits/i);
    const toNextMatch = text.match(/([\d,]+)\s*to advance to\s+(\w+)/i);
    const pointsMatch = text.match(/MGM Rewards Points\s*([\d,]+)/i) ||
                        text.match(/([\d,]+)\s*\$[\d.]+\s*in comps/i);
    const compsMatch = text.match(/\$([\d.]+)\s*in comps/i);
    const freeplayMatch = text.match(/FREEPLAY[®]?\s*\$([\d.]+)/i);
    const slotMatch = text.match(/SLOT DOLLARS[®]?\s*\$([\d.]+)/i);
    const giftMatch = text.match(/Holiday Gift Points\s*([\d,.]+)/i);
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

async function scrapeTrips(page) {
  console.log('📋 Scraping MGM trips...');
  await humanNavigate(page, 'https://www.mgmresorts.com/trips/');
  await randomDelay(2000, 3000);

  const trips = [];

  for (const tab of ['Upcoming', 'Past']) {
    try {
      await page.evaluate((t) => {
        const els = [...document.querySelectorAll('a, button, span')];
        const el = els.find(e => e.textContent.trim() === t);
        if (el) el.click();
      }, tab);
      await randomDelay(2000, 3000);

      const tabTrips = await page.evaluate((currentTab) => {
        const text = document.body.innerText;
        if (text.includes('Make some new memories')) return [];
        const results = [];
        const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
        for (let i = 0; i < lines.length; i++) {
          const confMatch = lines[i].match(/Confirmation[:\s#]+([A-Z0-9]+)/i);
          if (confMatch) {
            results.push({ confirmationCode: confMatch[1], tab: currentTab.toLowerCase() });
          }
        }
        return results;
      }, tab);

      trips.push(...tabTrips);
    } catch (e) {}
  }

  console.log(`  Found ${trips.length} trips`);
  return trips;
}

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
