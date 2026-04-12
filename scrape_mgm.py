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

from selenium import webdriver
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

# ── Debug helpers ────────────────────────────────────────────────────────────
def _save_debug(driver, name):
    """Save screenshot + page source for debugging."""
    os.makedirs('debug', exist_ok=True)
    try:
        driver.save_screenshot(f'debug/{name}.png')
    except Exception:
        pass
    try:
        with open(f'debug/{name}.html', 'w') as f:
            f.write(driver.page_source)
    except Exception:
        pass

# ── Login ─────────────────────────────────────────────────────────────────────
def _dismiss_cookie_banner(driver):
    """Dismiss cookie consent banner if present."""
    try:
        for btn in driver.find_elements(By.CSS_SELECTOR, 'button, a'):
            txt = btn.text.strip().lower()
            if txt in ('accept all', 'accept', 'accept cookies', 'i accept', 'got it', 'ok') and btn.is_displayed():
                human_click(driver, btn)
                print("  🍪 Dismissed cookie banner")
                human_delay(1, 2)
                return
    except Exception:
        pass

def _random_scroll(driver):
    """Scroll around a bit like a human would."""
    driver.execute_script("window.scrollBy(0, arguments[0]);", random.randint(100, 300))
    human_delay(0.5, 1.5)
    driver.execute_script("window.scrollBy(0, arguments[0]);", random.randint(-200, -50))
    human_delay(0.5, 1)

def mgm_login(driver):
    print("🔑 Logging in to MGM...")
    if load_cookies(driver, 'mgm', 'www.mgmresorts.com'):
        if verify_session(driver, 'https://www.mgmresorts.com/rewards/', 'Tier Credits'):
            print("  ✅ Session restored from cookies!")
            return
        else:
            print("  🍪 Cookies expired, doing full login...")

    # Navigate to homepage first like a real user
    driver.get('https://www.mgmresorts.com/')
    human_delay(4, 7)
    _dismiss_cookie_banner(driver)
    _random_scroll(driver)
    human_delay(2, 4)

    # Click "Sign In" link from the homepage instead of going directly to /identity/
    sign_in_clicked = False
    for el in driver.find_elements(By.CSS_SELECTOR, 'a, button, [role="button"]'):
        try:
            txt = el.text.strip().lower()
            href = (el.get_attribute('href') or '').lower()
            if ('sign in' in txt or 'log in' in txt or '/identity' in href) and el.is_displayed():
                print(f"  Clicking '{el.text.strip()}' link...")
                human_click(driver, el)
                sign_in_clicked = True
                break
        except Exception:
            continue

    if not sign_in_clicked:
        print("  Could not find Sign In link, navigating directly...")
        driver.get('https://www.mgmresorts.com/identity/?client_id=mgm_app_web&redirect_uri=https://www.mgmresorts.com/rewards/&scopes=')

    human_delay(5, 8)

    _save_debug(driver, 'mgm-login-page')
    print(f"  Login page URL: {driver.current_url}")
    print(f"  Login page title: {driver.title}")

    # Try multiple selectors for the email field
    wait = WebDriverWait(driver, 20)
    email_input = None
    for selector in [
        (By.ID, 'email'),
        (By.CSS_SELECTOR, 'input[type="email"]'),
        (By.CSS_SELECTOR, 'input[name="email"]'),
        (By.CSS_SELECTOR, 'input[name="username"]'),
        (By.CSS_SELECTOR, 'input[autocomplete="email"]'),
    ]:
        try:
            email_input = wait.until(EC.presence_of_element_located(selector))
            print(f"  Found email input via {selector}")
            break
        except TimeoutException:
            continue

    if not email_input:
        for inp in driver.find_elements(By.TAG_NAME, 'input'):
            itype = inp.get_attribute('type') or ''
            if itype in ('text', 'email') and inp.is_displayed():
                email_input = inp
                print(f"  Found email input via fallback (type={itype})")
                break

    if not email_input:
        _save_debug(driver, 'mgm-no-email-field')
        print("  ❌ Could not find email input field")
        print(f"  Page text preview: {driver.find_element(By.TAG_NAME, 'body').text[:500]}")
        raise Exception("MGM login failed: no email input found")

    # Click the field, pause, then type slowly
    email_input.click()
    human_delay(0.5, 1.0)
    human_type(email_input, os.environ['MGM_EMAIL'])
    human_delay(1.5, 3)

    _save_debug(driver, 'mgm-email-entered')

    # Click next/submit after email
    submit_btn = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
    human_click(driver, submit_btn)
    human_delay(4, 7)

    # Check for error message before waiting for password
    body_text = driver.find_element(By.TAG_NAME, 'body').text
    if 'unknown error' in body_text.lower() or 'contact support' in body_text.lower():
        _save_debug(driver, 'mgm-email-error')
        print("  ⚠️ MGM showed error after email submit, retrying with fresh page...")
        human_delay(3, 5)

        # Retry: go back and try again with longer delays
        driver.get('https://www.mgmresorts.com/')
        human_delay(5, 8)
        _dismiss_cookie_banner(driver)
        _random_scroll(driver)
        human_delay(3, 5)

        # Try clicking Sign In from homepage again
        for el in driver.find_elements(By.CSS_SELECTOR, 'a, button, [role="button"]'):
            try:
                txt = el.text.strip().lower()
                if ('sign in' in txt or 'log in' in txt) and el.is_displayed():
                    human_click(driver, el)
                    break
            except Exception:
                continue
        else:
            driver.get('https://www.mgmresorts.com/identity/?client_id=mgm_app_web&redirect_uri=https://www.mgmresorts.com/rewards/&scopes=')

        human_delay(6, 10)

        email_input = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, 'email'))
        )
        email_input.click()
        human_delay(1, 2)
        human_type(email_input, os.environ['MGM_EMAIL'])
        human_delay(2, 4)
        submit_btn = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        human_click(driver, submit_btn)
        human_delay(5, 8)

    # Wait for password field
    pass_input = None
    try:
        pass_input = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="password"]'))
        )
    except TimeoutException:
        _save_debug(driver, 'mgm-no-password-field')
        print(f"  ❌ No password field appeared. URL: {driver.current_url}")
        print(f"  Page text preview: {driver.find_element(By.TAG_NAME, 'body').text[:500]}")
        raise Exception("MGM login failed: no password field")

    human_type(pass_input, os.environ['MGM_PASSWORD'])
    human_delay(1.5, 3)
    submit_btn = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
    human_click(driver, submit_btn)
    human_delay(5, 8)

    _save_debug(driver, 'mgm-after-login')
    print(f"  URL after login: {driver.current_url}")

    if '/identity' in driver.current_url:
        print("  ⚠️ Still on login page — login may have failed")
        print(f"  Page text: {driver.find_element(By.TAG_NAME, 'body').text[:500]}")

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
    from selenium.webdriver.chrome.service import Service
    options = webdriver.ChromeOptions()
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--lang=en-US')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    if visible:
        options.add_argument('--start-maximized')
    # Run under xvfb on CI instead of --headless.
    # MGM doesn't need undetected-chromedriver (which injects a console
    # fingerprint that MGM's LogRocket detects, causing 403 on their
    # identity API).
    return webdriver.Chrome(options=options)

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
