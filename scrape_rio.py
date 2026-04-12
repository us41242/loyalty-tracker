#!/usr/bin/env python3
"""
Rio Rewards scraper — standalone script.
Launches Chrome, logs in, scrapes rewards/offers, saves to Supabase.
Includes retry logic (3 attempts with page refresh) for slow-loading pages.

Usage:
    python scrape_rio.py [--visible]
"""

import os
import re
import sys
import time
import random
import json
from datetime import datetime, date, timezone, timedelta

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
supabase = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

PACIFIC = timezone(timedelta(hours=-7))  # PDT
RUN_TS = datetime.now(PACIFIC).isoformat()

# ── Cookie Session Management ─────────────────────────────────────────────────
def save_cookies(driver, site):
    try:
        cookies = driver.get_cookies()
        supabase.table('session_cookies').upsert({
            'site': site,
            'cookies': json.dumps(cookies),
            'updated_at': datetime.now().isoformat(),
        }, on_conflict='site').execute()
        print(f"  🍪 Saved {len(cookies)} cookies for {site}")
    except Exception as e:
        print(f"  ⚠️ Could not save cookies for {site}: {e}")

def load_cookies(driver, site, domain):
    try:
        result = supabase.table('session_cookies').select('cookies, updated_at').eq('site', site).execute()
        if not result.data:
            print(f"  🍪 No saved cookies for {site}")
            return False
        row = result.data[0]
        cookies = json.loads(row['cookies'])
        print(f"  🍪 Loading {len(cookies)} cookies for {site} (saved: {row['updated_at'][:19]})")
        if not cookies:
            return False
        driver.get(f'https://{domain}/')
        human_delay(3, 5)
        loaded = 0
        for cookie in cookies:
            for key in ['sameSite', 'expiry', 'httpOnly', 'storeId']:
                cookie.pop(key, None)
            if 'domain' in cookie and domain not in cookie['domain']:
                continue
            try:
                driver.add_cookie(cookie)
                loaded += 1
            except Exception:
                pass
        print(f"  🍪 Loaded {loaded}/{len(cookies)} cookies")
        return loaded > 0
    except Exception as e:
        print(f"  🍪 Could not load cookies for {site}: {e}")
        return False

def verify_session(driver, check_url, success_indicator):
    try:
        driver.get(check_url)
        human_delay(5, 8)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
        return success_indicator.lower() in driver.find_element(By.TAG_NAME, 'body').text.lower()
    except Exception as e:
        print(f"  🍪 Session verify failed: {e}")
        return False

# ── Human-like helpers ────────────────────────────────────────────────────────
def human_delay(min_s=1.0, max_s=3.0):
    time.sleep(random.uniform(min_s, max_s))

def human_type(element, text, min_delay=0.05, max_delay=0.15):
    element.click()
    human_delay(0.3, 0.7)
    element.clear()
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(min_delay, max_delay))
    human_delay(0.3, 0.8)

def human_click(driver, element):
    from selenium.webdriver.common.action_chains import ActionChains
    actions = ActionChains(driver)
    actions.move_to_element_with_offset(element, random.randint(-3, 3), random.randint(-3, 3))
    human_delay(0.1, 0.3)
    actions.click()
    actions.perform()
    human_delay(0.5, 1.5)

# ── Date helper ───────────────────────────────────────────────────────────────
def parse_date(date_str):
    if not date_str:
        return None
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', date_str)
    if m:
        return f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    months = {'Jan':'01','Feb':'02','Mar':'03','Apr':'04','May':'05','Jun':'06',
              'Jul':'07','Aug':'08','Sep':'09','Oct':'10','Nov':'11','Dec':'12'}
    m = re.match(r'([A-Z][a-z]{2})\s+(\d{1,2}),?\s*(\d{4})', date_str)
    if m and m.group(1) in months:
        return f"{m.group(3)}-{months[m.group(1)]}-{m.group(2).zfill(2)}"
    return date_str

# ── Login ─────────────────────────────────────────────────────────────────────
def rio_login(driver):
    print("🔑 Logging in to Rio...")
    if load_cookies(driver, 'rio', 'www.riolasvegas.com'):
        if verify_session(driver, 'https://www.riolasvegas.com/rio-rewards/offers', 'RIO REWARDS POINTS'):
            print("  ✅ Session restored from cookies!")
            return
        else:
            print("  🍪 Cookies expired, doing full login...")

    driver.get('https://www.riolasvegas.com/api/auth/login?returnTo=/rio-rewards/offers')
    human_delay(3, 5)

    try:
        email_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'input[name="email"], input[type="email"], input[name="username"]')
            )
        )
        human_type(email_input, os.environ['RIO_USERNAME'])
        human_delay(0.5, 1)
        pass_input = driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
        human_type(pass_input, os.environ['RIO_PASSWORD'])
        human_delay(0.8, 1.5)
        submit = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        human_click(driver, submit)
        human_delay(5, 8)
    except Exception as e:
        print(f"  Login flow: {e}")

    print(f"  URL after login: {driver.current_url}")
    save_cookies(driver, 'rio')

# ── Scraping ──────────────────────────────────────────────────────────────────
def scrape_rio_rewards(driver):
    if '/rio-rewards/offers' not in driver.current_url:
        driver.get('https://www.riolasvegas.com/rio-rewards/offers')
        human_delay(5, 8)  # Rio loads slowly — give it extra time

    # Wait for meaningful content to appear
    for _ in range(6):
        text = driver.find_element(By.TAG_NAME, 'body').text
        if 'RIO REWARDS POINTS' in text.upper() or 'ROUGE' in text.upper() or 'AZUL' in text.upper():
            break
        print("  Waiting for Rio page to load...")
        human_delay(5, 8)
    else:
        text = driver.find_element(By.TAG_NAME, 'body').text

    print("📊 Scraping Rio rewards and offers...")

    snapshot = {}
    m = re.search(r'(ROUGE|AZUL|GOLD|PLATINUM)\s+MEMBER\s*\|\s*#(\d+)', text, re.I)
    snapshot['tier_status'] = m.group(1) if m else None
    snapshot['member_number'] = m.group(2) if m else None

    m = re.search(r'([\d,]+)\s*RIO REWARDS POINTS', text, re.I)
    snapshot['points_balance'] = int(m.group(1).replace(',', '')) if m else None

    m = re.search(r'\$([\d,.]+)\s*RESORT CREDIT', text, re.I)
    snapshot['resort_credit'] = float(m.group(1)) if m else None

    m = re.search(r'([\d,]+)\s*POINTS EARNED IN \d{4}', text, re.I)
    snapshot['points_earned_year'] = int(m.group(1).replace(',', '')) if m else None

    m = re.search(r'([\d,]+)\s*POINTS TO (\w+)', text, re.I)
    snapshot['points_to_next_tier'] = int(m.group(1).replace(',', '')) if m else None
    snapshot['next_tier'] = m.group(2) if m else None

    m = re.search(r'earned.*?status through\s+([\w\s]+\d{4})', text, re.I)
    snapshot['status_valid_through'] = m.group(1).strip() if m else None

    offers = []
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for i, line in enumerate(lines):
        dm = re.match(r'(?:Offer Valid|Book Offer|Stay Dates):\s*(.+)', line, re.I)
        if dm:
            title = ''
            for j in range(i-1, max(0, i-5), -1):
                if len(lines[j]) > 5 and not re.match(r'^(Book|Stay|Offer|Valid)', lines[j], re.I):
                    title = lines[j]
                    break
            if title:
                offers.append({'title': title, 'dates': dm.group(1)})

    print(f"  Tier: {snapshot['tier_status']} | Points: {snapshot['points_balance']}")
    print(f"  Found {len(offers)} offers")
    return {'snapshot': snapshot, 'offers': offers}

# ── Save to Supabase ──────────────────────────────────────────────────────────
def save_rio_snapshot(data):
    supabase.table('rio_rewards_snapshots').insert({
        'run_ts': RUN_TS,
        'tier_status': data.get('tier_status'),
        'member_number': data.get('member_number'),
        'points_balance': data.get('points_balance'),
        'resort_credit': data.get('resort_credit'),
        'points_earned_year': data.get('points_earned_year'),
        'points_to_next_tier': data.get('points_to_next_tier'),
        'next_tier': data.get('next_tier'),
        'status_valid_through': parse_date(data.get('status_valid_through')),
    }).execute()
    print("  💾 Saved Rio snapshot")

def save_rio_offers(offers):
    saved = 0
    seen = set()
    for o in offers:
        if not o.get('title'):
            continue
        parts = o.get('dates', '').split(' - ') if o.get('dates') else [None, None]
        valid_start = date.today().isoformat() if parts[0] == 'Now' else (parse_date(parts[0]) if parts else None)
        valid_end = parse_date(parts[1]) if len(parts) > 1 else None
        key = (o['title'], valid_end)
        if key in seen:
            continue
        seen.add(key)
        try:
            supabase.table('rio_offers').upsert({
                'run_ts': RUN_TS,
                'title': o['title'],
                'valid_start': valid_start,
                'valid_end': valid_end,
                'last_seen': datetime.now().isoformat(),
            }, on_conflict='title,valid_end').execute()
            saved += 1
        except Exception:
            pass
    print(f"  💾 Saved {saved} offers")

# ── Browser setup ─────────────────────────────────────────────────────────────
def make_driver(visible=False):
    options = uc.ChromeOptions()
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--lang=en-US')
    if visible:
        options.add_argument('--start-maximized')
    # DO NOT use --headless — bot protection detects it.
    # On CI, xvfb provides a virtual display instead.

    options.binary_location = '/usr/bin/google-chrome-stable'

    chrome_ver = None
    ver_str = os.environ.get('CHROME_VERSION', '')
    if ver_str.isdigit():
        chrome_ver = int(ver_str)
        print(f"  Chrome version from env: {chrome_ver}")

    if chrome_ver:
        return uc.Chrome(options=options, use_subprocess=True, version_main=chrome_ver)
    return uc.Chrome(options=options, use_subprocess=True)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--visible', action='store_true', help='Open a visible Chrome window')
    args = parser.parse_args()
    visible = args.visible or bool(os.environ.get('LOCAL_DEBUG'))

    start = time.time()
    print("🎰 Rio Rewards Scraper")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} PT")
    if visible:
        print("   👁  Visible mode\n")

    driver = make_driver(visible)
    exit_code = 0
    try:
        # Rio loads slowly — retry up to 3 times with a refresh between attempts
        last_exc = None
        for attempt in range(3):
            try:
                if attempt == 0:
                    rio_login(driver)
                else:
                    print(f"\n🔄 Retry {attempt}/2 — refreshing page...")
                    driver.refresh()
                    human_delay(12, 18)  # Extra wait for slow Rio pages

                data = scrape_rio_rewards(driver)

                # Validate we got something useful before saving
                if data['snapshot'].get('points_balance') is None and data['snapshot'].get('tier_status') is None:
                    raise Exception("Page loaded but no rewards data found — may still be loading")

                save_rio_snapshot(data['snapshot'])
                save_rio_offers(data['offers'])
                print("\n✅ Rio scrape complete!")
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                import traceback; traceback.print_exc()
                if attempt < 2:
                    print(f"  ⚠️ Attempt {attempt+1}/3 failed: {e}")

        if last_exc is not None:
            print(f"\n❌ Rio scrape failed after 3 attempts: {last_exc}")
            exit_code = 1

    except Exception as e:
        import traceback
        print(f"\n❌ Rio scrape failed: {e}")
        traceback.print_exc()
        exit_code = 1
    finally:
        driver.quit()
        print(f"🏁 Done in {time.time() - start:.1f}s")

    sys.exit(exit_code)

if __name__ == '__main__':
    main()
