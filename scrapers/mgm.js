const { supabase, parseDate } = require('./db');
const { createPage, humanType, humanClick, humanNavigate, randomDelay } = require('./browser');
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
  await humanNavigate(page, 'https://www.mgmresorts.com/identity/?client_id=mgm_app_web&redirect_uri=https://www.mgmresorts.com/rewards/&scopes=');
  await randomDelay(3000, 5000);

  // Debug: what's on the page?
  const inputInfo = await page.evaluate(() => {
    const inputs = [...document.querySelectorAll('input')];
    return inputs.map(i => ({
      type: i.type, name: i.name, id: i.id, placeholder: i.placeholder,
      visible: i.offsetWidth > 0 && i.offsetHeight > 0,
      ariaLabel: i.getAttribute('aria-label'),
    }));
  });
  console.log('  Inputs found:', JSON.stringify(inputInfo));

  const buttonInfo = await page.evaluate(() => {
    const buttons = [...document.querySelectorAll('button, input[type="submit"]')];
    return buttons.map(b => ({
      text: b.textContent.trim().substring(0, 40), type: b.type,
      visible: b.offsetWidth > 0 && b.offsetHeight > 0,
    }));
  });
  console.log('  Buttons found:', JSON.stringify(buttonInfo));

  // Check for bot challenge
  const pageText = await page.evaluate(() => document.body.innerText.substring(0, 300));
  console.log('  Page text preview:', pageText.replace(/\n/g, ' ').substring(0, 200));

  await debugPage(page, 'mgm-login-page');

  try {
    // Find any visible input that could be email
    const emailSelector = inputInfo.find(i => i.visible && i.type !== 'password' && i.type !== 'hidden');
    if (emailSelector) {
      const sel = emailSelector.id ? `#${emailSelector.id}` :
                  emailSelector.name ? `input[name="${emailSelector.name}"]` :
                  `input[type="${emailSelector.type}"]`;
      console.log(`  Using email selector: ${sel}`);

      await page.click(sel, { clickCount: 3 });
      await randomDelay(200, 400);
      await page.keyboard.type(process.env.MGM_EMAIL, { delay: Math.floor(Math.random() * 100) + 60 });
      await randomDelay(800, 1500);

      // Look for Next/Continue/Sign In button
      const signInClicked = await page.evaluate(() => {
        const buttons = [...document.querySelectorAll('button')];
        const btn = buttons.find(b =>
          b.offsetWidth > 0 &&
          (b.textContent.trim().match(/^(Next|Continue|Sign In|Log In|Submit)$/i))
        );
        if (btn) { btn.click(); return btn.textContent.trim(); }
        return null;
      });
      console.log(`  Clicked: ${signInClicked || 'none'}`);
      await randomDelay(2000, 4000);

      // Check for password field appearing
      const passInput = await page.$('input[type="password"]');
      if (passInput) {
        await page.click('input[type="password"]', { clickCount: 3 });
        await randomDelay(200, 400);
        await page.keyboard.type(process.env.MGM_PASSWORD, { delay: Math.floor(Math.random() * 100) + 60 });
        await randomDelay(800, 1500);

        const submitted = await page.evaluate(() => {
          const buttons = [...document.querySelectorAll('button')];
          const btn = buttons.find(b =>
            b.offsetWidth > 0 &&
            b.textContent.trim().match(/^(Sign In|Log In|Submit)$/i)
          );
          if (btn) { btn.click(); return true; }
          return false;
        });
        if (!submitted) await page.keyboard.press('Enter');

        await randomDelay(5000, 8000);
      }
    } else {
      console.log('  ⚠️ No visible email input found');
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
