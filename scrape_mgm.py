#!/usr/bin/env python3
"""
MGM Rewards scraper — standalone script.
Launches Chrome, logs in, scrapes rewards/trips, saves to Supabase.

Usage:
    python scrape_mgm.py [--visible]
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
def mgm_login(driver):
    print("🔑 Logging in to MGM...")
    if load_cookies(driver, 'mgm', 'www.mgmresorts.com'):
        if verify_session(driver, 'https://www.mgmresorts.com/rewards/', 'Tier Credits'):
            print("  ✅ Session restored from cookies!")
            return
        else:
            print("  🍪 Cookies expired, doing full login...")

    driver.get('https://www.mgmresorts.com/')
    human_delay(3, 5)
    driver.get('https://www.mgmresorts.com/identity/?client_id=mgm_app_web&redirect_uri=https://www.mgmresorts.com/rewards/&scopes=')
    human_delay(3, 6)

    try:
        email_input = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, 'email'))
        )
        human_type(email_input, os.environ['MGM_EMAIL'])
        human_delay(1, 2)
        next_btn = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        human_click(driver, next_btn)
        human_delay(3, 5)
        pass_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="password"]'))
        )
        human_type(pass_input, os.environ['MGM_PASSWORD'])
        human_delay(1, 2)
        submit_btn = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        human_click(driver, submit_btn)
        human_delay(5, 8)
    except Exception as e:
        print(f"  Login flow: {e}")

    print(f"  URL after login: {driver.current_url}")
    save_cookies(driver, 'mgm')

# ── Scraping ──────────────────────────────────────────────────────────────────
def scrape_mgm_rewards(driver):
    print("📊 Scraping MGM rewards...")
    if '/rewards' not in driver.current_url:
        driver.get('https://www.mgmresorts.com/rewards/')
        human_delay(3, 5)
    text = driver.find_element(By.TAG_NAME, 'body').text
    data = {}
    m = re.search(r'(Sapphire|Pearl|Gold|Platinum|Noir)', text, re.I)
    data['tier_status'] = m.group(1) if m else None
    m = re.search(r'([\d,]+)\s*Tier Credits', text, re.I)
    data['tier_credits'] = int(m.group(1).replace(',', '')) if m else None
    m = re.search(r'([\d,]+)\s*to advance to\s+(\w+)', text, re.I)
    data['tier_credits_to_next'] = int(m.group(1).replace(',', '')) if m else None
    data['tier_next'] = m.group(2) if m else None
    m = re.search(r'MGM Rewards Points\s*([\d,]+)', text, re.I) or \
        re.search(r'([\d,]+)\s*\$[\d.]+\s*in comps', text, re.I)
    data['rewards_points'] = int(m.group(1).replace(',', '')) if m else None
    m = re.search(r'\$([\d.]+)\s*in comps', text, re.I)
    data['rewards_comps_value'] = float(m.group(1)) if m else None
    m = re.search(r'FREEPLAY[®]?\s*\$([\d.]+)', text, re.I)
    data['freeplay'] = float(m.group(1)) if m else None
    m = re.search(r'SLOT DOLLARS[®]?\s*\$([\d.]+)', text, re.I)
    data['slot_dollars'] = float(m.group(1)) if m else None
    m = re.search(r'Holiday Gift Points\s*([\d,.]+)', text, re.I)
    data['holiday_gift_points'] = float(m.group(1).replace(',', '')) if m else None
    m = re.search(r'(\d+)\s*Milestone Rewards', text, re.I)
    data['milestone_rewards'] = int(m.group(1)) if m else None
    print(f"  Tier: {data['tier_status']} | Credits: {data['tier_credits']} | Points: {data['rewards_points']}")
    return data

def scrape_mgm_trips(driver):
    print("📋 Scraping MGM trips...")
    driver.get('https://www.mgmresorts.com/trips/')
    human_delay(3, 4)
    text = driver.find_element(By.TAG_NAME, 'body').text
    if 'Make some new memories' in text:
        print("  No trips found")
        return []
    trips = []
    for line in [l.strip() for l in text.split('\n') if l.strip()]:
        m = re.search(r'Confirmation[:\s#]+([A-Z0-9]+)', line, re.I)
        if m:
            trips.append({'confirmation_code': m.group(1)})
    print(f"  Found {len(trips)} trips")
    return trips

# ── Save to Supabase ──────────────────────────────────────────────────────────
def save_mgm_snapshot(data):
    supabase.table('mgm_rewards_snapshots').insert({
        'run_ts': RUN_TS,
        'tier_status': data.get('tier_status'),
        'tier_credits': data.get('tier_credits'),
        'tier_credits_to_next': data.get('tier_credits_to_next'),
        'tier_next': data.get('tier_next'),
        'rewards_points': data.get('rewards_points'),
        'rewards_comps_value': data.get('rewards_comps_value'),
        'freeplay': data.get('freeplay'),
        'slot_dollars': data.get('slot_dollars'),
        'holiday_gift_points': data.get('holiday_gift_points'),
        'milestone_rewards': data.get('milestone_rewards'),
    }).execute()
    print("  💾 Saved MGM snapshot")

def save_mgm_trips(trips):
    for t in trips:
        if not t.get('confirmation_code'):
            continue
        supabase.table('mgm_trips').upsert({
            'run_ts': RUN_TS,
            'confirmation_code': t['confirmation_code'],
            'tab': 'past',
            'updated_at': datetime.now().isoformat(),
        }, on_conflict='confirmation_code').execute()
    if trips:
        print(f"  💾 Saved {len(trips)} trips")

# ── Browser setup ─────────────────────────────────────────────────────────────
def make_driver(visible=False):
    import subprocess, platform
    options = uc.ChromeOptions()
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--lang=en-US')
    if visible:
        options.add_argument('--start-maximized')
    # DO NOT use --headless — detected by bot protection
    # Use xvfb on Linux CI for a virtual display instead

    chrome_ver = None
    try:
        if platform.system() == 'Darwin':
            result = subprocess.run(
                ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--version'],
                capture_output=True, text=True)
        else:
            result = subprocess.run(['google-chrome', '--version'], capture_output=True, text=True)
            if result.returncode != 0:
                result = subprocess.run(['chromium', '--version'], capture_output=True, text=True)
        m = re.search(r'(\d+)\.', result.stdout)
        chrome_ver = int(m.group(1)) if m else None
    except Exception:
        pass

    print(f"  Chrome version: {chrome_ver or 'auto-detect'}")
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
    print("🎰 MGM Rewards Scraper")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} PT")
    if visible:
        print("   👁  Visible mode\n")

    driver = make_driver(visible)
    exit_code = 0
    try:
        mgm_login(driver)
        rewards = scrape_mgm_rewards(driver)
        trips = scrape_mgm_trips(driver)
        save_mgm_snapshot(rewards)
        save_mgm_trips(trips)
        print("\n✅ MGM scrape complete!")
    except Exception as e:
        import traceback
        print(f"\n❌ MGM scrape failed: {e}")
        traceback.print_exc()
        exit_code = 1
    finally:
        driver.quit()
        print(f"🏁 Done in {time.time() - start:.1f}s")

    sys.exit(exit_code)

if __name__ == '__main__':
    main()
