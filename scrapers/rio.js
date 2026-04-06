const { supabase, parseDate } = require('./db');
const { createPage, humanType, humanClick, humanNavigate, randomDelay } = require('./browser');

async function scrapeRio(browser) {
  console.log('\n═══════════════════════════════════════');
  console.log('  RIO REWARDS SCRAPER');
  console.log('═══════════════════════════════════════\n');

  const page = await createPage(browser);

  try {
    await login(page);
    const data = await scrapeRewardsAndOffers(page);
    await saveSnapshot(data.snapshot);
    await saveOffers(data.offers);
    console.log('\n✅ Rio scrape complete!\n');
  } catch (err) {
    console.error('❌ Rio error:', err.message);
  } finally {
    await page.close();
  }
}

async function login(page) {
  console.log('🔑 Logging in to Rio...');
  await humanNavigate(page, 'https://www.riolasvegas.com/api/auth/login?returnTo=/rio-rewards/offers');
  await randomDelay(2000, 4000);

  try {
    const emailInput = await page.$('input[name="email"], input[type="email"], input[name="username"]');
    if (emailInput) {
      await humanType(page, 'input[name="email"], input[type="email"], input[name="username"]', process.env.RIO_USERNAME);
      await randomDelay(500, 1000);
      await humanType(page, 'input[type="password"]', process.env.RIO_PASSWORD);
      await randomDelay(800, 1500);
      await humanClick(page, 'button[type="submit"]');
      await randomDelay(4000, 7000);
    }
  } catch (e) {
    console.log('  May already be logged in or different flow');
  }

  console.log('  URL after login:', page.url());
}

async function scrapeRewardsAndOffers(page) {
  if (!page.url().includes('/rio-rewards/offers')) {
    await humanNavigate(page, 'https://www.riolasvegas.com/rio-rewards/offers');
  }
  await randomDelay(2000, 4000);

  console.log('📊 Scraping Rio rewards and offers...');

  const data = await page.evaluate(() => {
    const text = document.body.innerText;

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

    const offers = [];
    const offerSection = text.split(/Your Offers/i)[1] || '';
    const lines = offerSection.split('\n').map(l => l.trim()).filter(Boolean);

    let i = 0;
    while (i < lines.length) {
      const dateMatch = lines[i].match(/(?:Offer Valid|Book Offer|Stay Dates):\s*(.+)/i);
      if (dateMatch) {
        let title = '';
        for (let j = i - 1; j >= Math.max(0, i - 5); j--) {
          if (lines[j].length > 5 && !lines[j].match(/^(Book|Stay|Offer|Valid)/i)) {
            title = lines[j];
            break;
          }
        }
        if (title) {
          offers.push({ title, dates: dateMatch[1], description: null });
        }
      }
      i++;
    }

    return { snapshot, offers };
  });

  console.log(`  Tier: ${data.snapshot.tierStatus} | Points: ${data.snapshot.pointsBalance}`);
  console.log(`  Found ${data.offers.length} offers`);
  return data;
}

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
