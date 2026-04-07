#!/usr/bin/env python3
"""
Caesars Rewards scraper — standalone script.
Launches Chrome, logs in, scrapes rewards/reservations/offers/great-gift, saves to Supabase.

Usage:
    python scrape_caesars.py [--visible]
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
from selenium.webdriver.common.keys import Keys
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

# ── Date helpers ──────────────────────────────────────────────────────────────
def parse_date(date_str):
    """Parse various date formats to YYYY-MM-DD."""
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

def parse_caesars_dates(dates_str):
    """
    Parse Caesars offer date strings into (valid_start, valid_end, expires_at).
    Relative strings are resolved to actual calendar dates at call time.

    Examples (assuming today is 2026-04-07):
        "Expires today"             -> (None, None, "2026-04-07")
        "Expires tomorrow"          -> (None, None, "2026-04-08")
        "Expires in 2 days"         -> (None, None, "2026-04-09")
        "Expires 04.30.26"          -> (None, None, "2026-04-30")
        "Valid 04.07.26 - 04.30.26" -> ("2026-04-07", "2026-04-30", "2026-04-30")
    """
    if not dates_str:
        return None, None, None

    today = date.today()

    def _parse_mmdyy(s):
        m = re.match(r'(\d{2})\.(\d{2})\.(\d{2})', s.strip())
        if m:
            return date(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))
        return None

    if re.match(r'expires?\s+today', dates_str, re.IGNORECASE):
        return None, None, today.isoformat()

    if re.match(r'expires?\s+tomorrow', dates_str, re.IGNORECASE):
        return None, None, (today + timedelta(days=1)).isoformat()

    m = re.match(r'expires?\s+in\s+(\d+)\s+days?', dates_str, re.IGNORECASE)
    if m:
        return None, None, (today + timedelta(days=int(m.group(1)))).isoformat()

    m = re.match(r'valid\s+(\d{2}\.\d{2}\.\d{2})\s*-\s*(\d{2}\.\d{2}\.\d{2})', dates_str, re.IGNORECASE)
    if m:
        start = _parse_mmdyy(m.group(1))
        end = _parse_mmdyy(m.group(2))
        return (start.isoformat() if start else None,
                end.isoformat() if end else None,
                end.isoformat() if end else None)

    m = re.match(r'expires?\s+(\d{2}\.\d{2}\.\d{2})', dates_str, re.IGNORECASE)
    if m:
        exp = _parse_mmdyy(m.group(1))
        return None, None, exp.isoformat() if exp else None

    return None, None, None

# ── 2FA helpers ───────────────────────────────────────────────────────────────
_last_used_mfa_message_id = None

def _prime_last_mfa_id():
    global _last_used_mfa_message_id
    import urllib.request, urllib.parse
    client_id = os.environ.get('GMAIL_CLIENT_ID')
    client_secret = os.environ.get('GMAIL_CLIENT_SECRET')
    refresh_token = os.environ.get('GMAIL_REFRESH_TOKEN')
    if not all([client_id, client_secret, refresh_token]):
        return
    try:
        data = urllib.parse.urlencode({
            'client_id': client_id, 'client_secret': client_secret,
            'refresh_token': refresh_token, 'grant_type': 'refresh_token',
        }).encode()
        req = urllib.request.Request('https://oauth2.googleapis.com/token', data=data)
        resp = json.loads(urllib.request.urlopen(req).read())
        access_token = resp.get('access_token')
        if not access_token:
            return
        query = 'from:email@email.caesars-marketing.com subject:"MFA Code" newer_than:1h'
        url = f'https://gmail.googleapis.com/gmail/v1/users/me/messages?q={urllib.parse.quote(query)}&maxResults=1'
        req = urllib.request.Request(url)
        req.add_header('Authorization', f'Bearer {access_token}')
        resp = json.loads(urllib.request.urlopen(req).read())
        messages = resp.get('messages', [])
        if messages:
            _last_used_mfa_message_id = messages[0]['id']
            print(f"  Primed last MFA message ID: {_last_used_mfa_message_id[:10]}...")
    except Exception as e:
        print(f"  Could not prime MFA ID: {e}")

def get_2fa_code():
    global _last_used_mfa_message_id
    import urllib.request, urllib.parse
    client_id = os.environ.get('GMAIL_CLIENT_ID')
    client_secret = os.environ.get('GMAIL_CLIENT_SECRET')
    refresh_token = os.environ.get('GMAIL_REFRESH_TOKEN')
    if not all([client_id, client_secret, refresh_token]):
        return input("  Enter 2FA code from email: ").strip()
    data = urllib.parse.urlencode({
        'client_id': client_id, 'client_secret': client_secret,
        'refresh_token': refresh_token, 'grant_type': 'refresh_token',
    }).encode()
    req = urllib.request.Request('https://oauth2.googleapis.com/token', data=data)
    resp = json.loads(urllib.request.urlopen(req).read())
    access_token = resp.get('access_token')
    if not access_token:
        print("  ❌ Could not refresh Gmail token")
        return input("  Enter 2FA code from email: ").strip()
    print("  ⏳ Polling Gmail for MFA code...")
    for attempt in range(24):
        time.sleep(5)
        try:
            query = 'from:email@email.caesars-marketing.com subject:"MFA Code" newer_than:5m'
            url = f'https://gmail.googleapis.com/gmail/v1/users/me/messages?q={urllib.parse.quote(query)}&maxResults=1'
            req = urllib.request.Request(url)
            req.add_header('Authorization', f'Bearer {access_token}')
            resp = json.loads(urllib.request.urlopen(req).read())
            messages = resp.get('messages', [])
            if messages:
                msg_id = messages[0]['id']
                if msg_id == _last_used_mfa_message_id:
                    print(f"  Attempt {attempt+1}/24 - same code as before, waiting...")
                    continue
                msg_url = f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=full'
                req2 = urllib.request.Request(msg_url)
                req2.add_header('Authorization', f'Bearer {access_token}')
                msg = json.loads(urllib.request.urlopen(req2).read())
                code_match = re.search(r'\b(\d{6})\b', msg.get('snippet', ''))
                if code_match:
                    code = code_match.group(1)
                    _last_used_mfa_message_id = msg_id
                    print(f"  📧 Got 2FA code: {code}")
                    return code
            else:
                print(f"  Attempt {attempt+1}/24 - no MFA email yet...")
        except Exception as e:
            print(f"  Attempt {attempt+1}/24 - {e}")
    return input("  Enter 2FA code from email: ").strip()

# ── Login ─────────────────────────────────────────────────────────────────────
def caesars_login(driver):
    print("🔑 Logging in to Caesars...")
    # Skip cookie loading for Caesars — Imperva blocks cookie-injected sessions
    driver.get('https://www.caesars.com/myrewards/profile/signin/')
    human_delay(3, 5)

    wait = WebDriverWait(driver, 20)
    try:
        user_input = wait.until(EC.presence_of_element_located((By.NAME, "userID")))
        human_type(user_input, os.environ['CAESARS_USERNAME'])
        print("  Username entered")
    except Exception:
        for inp in driver.find_elements(By.TAG_NAME, 'input'):
            if inp.get_attribute('type') in ('text', 'email', None) and inp.is_displayed():
                human_type(inp, os.environ['CAESARS_USERNAME'])
                print("  Username entered (fallback)")
                break

    human_delay(0.8, 1.5)
    pass_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="password"]')))
    human_type(pass_input, os.environ['CAESARS_PASSWORD'])
    print("  Password entered")
    human_delay(1, 2)

    for btn in driver.find_elements(By.TAG_NAME, 'button'):
        if btn.text.strip() in ('SIGN IN', 'Sign In') and btn.is_displayed():
            human_click(driver, btn)
            print("  Clicked Sign In")
            break

    human_delay(5, 8)
    print(f"  URL after login: {driver.current_url}")

    if '/verification/step-up' in driver.current_url:
        handle_caesars_2fa(driver)

    save_cookies(driver, 'caesars')

def handle_caesars_2fa(driver):
    print("🔐 2FA required...")
    _prime_last_mfa_id()
    human_delay(8, 12)
    code = get_2fa_code()
    if not code:
        raise Exception("Could not get 2FA code")
    print(f"  Entering code: {code}")
    try:
        first_input_xpath = '//*[@id="root"]/div/div/div[1]/div/div/div[2]/div[1]/div[4]/div[2]/div[1]/div/input[1]'
        first_input = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, first_input_xpath))
        )
        first_input.click()
        human_delay(0.3, 0.6)
    except Exception:
        inputs = driver.find_elements(By.CSS_SELECTOR, 'input[maxlength="1"]')
        if inputs:
            inputs[0].click()
            human_delay(0.3, 0.6)
    from selenium.webdriver.common.action_chains import ActionChains
    actions = ActionChains(driver)
    for digit in code:
        actions.send_keys(digit)
        actions.pause(random.uniform(0.15, 0.35))
    actions.perform()
    human_delay(1, 2)
    print("  Waiting for verification...")
    for _ in range(20):
        human_delay(2, 3)
        if '/verification/step-up' not in driver.current_url:
            break
    print(f"  ✅ 2FA complete, URL: {driver.current_url}")

# ── Scraping ──────────────────────────────────────────────────────────────────
def scrape_caesars_rewards(driver):
    print("📊 Scraping rewards home...")
    driver.get('https://www.caesars.com/rewards/home')
    human_delay(3, 5)
    text = driver.find_element(By.TAG_NAME, 'body').text
    data = {}
    m = re.search(r'([\d,]+)\s*REWARD CREDITS', text, re.I)
    data['reward_credits'] = int(m.group(1).replace(',', '')) if m else None
    m = re.search(r'([\d,]+)\s*TIER CREDITS', text, re.I)
    data['tier_credits'] = int(m.group(1).replace(',', '')) if m else None
    m = re.search(r'(SEVEN STARS|DIAMOND ELITE|DIAMOND PLUS|DIAMOND|PLATINUM|GOLD)', text, re.I)
    data['tier_status'] = m.group(1) if m else None
    m = re.search(r'([\d,]+)\s*to\s+(Seven Stars|Diamond Elite|Diamond Plus|Diamond|Platinum|Gold)', text, re.I)
    data['tier_credits_needed'] = int(m.group(1).replace(',', '')) if m else None
    data['tier_next'] = m.group(2) if m else None
    m = re.search(r'Last credits earned:\s*(\d{2}/\d{2}/\d{4})', text, re.I)
    data['last_earned_date'] = m.group(1) if m else None
    m = re.search(r'Earn more Reward Credits before\s*(\d{2}/\d{2}/\d{4})', text, re.I)
    data['credits_expire_date'] = m.group(1) if m else None
    print(f"  Credits: {data['reward_credits']} | Tier: {data['tier_credits']} {data['tier_status']}")
    return data

def scrape_caesars_reservations(driver, tab='past'):
    print(f"📋 Scraping {tab} reservations...")
    driver.get('https://www.caesars.com/rewards/stays')
    human_delay(2, 4)
    try:
        for link in driver.find_elements(By.CSS_SELECTOR, 'a, button, span'):
            if link.text.strip().upper() == tab.upper() and link.is_displayed():
                link.click()
                human_delay(2, 3)
                break
    except Exception:
        pass
    text = driver.find_element(By.TAG_NAME, 'body').text
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    reservations = []
    i = 0
    while i < len(lines):
        if lines[i] == 'Property':
            card = {'tab': tab}
            for j in range(i, min(i + 20, len(lines))):
                if lines[j] == 'Property':     card['property'] = lines[j+1] if j+1 < len(lines) else None
                if lines[j] == 'Location':     card['location'] = lines[j+1] if j+1 < len(lines) else None
                if lines[j] == 'Check-In':     card['check_in'] = lines[j+1] if j+1 < len(lines) else None
                if lines[j] == 'Checkout':     card['check_out'] = lines[j+1] if j+1 < len(lines) else None
                if lines[j] == 'Adults':       card['adults'] = int(lines[j+1]) if j+1 < len(lines) and lines[j+1].isdigit() else None
                if lines[j] == 'Children':     card['children'] = int(lines[j+1]) if j+1 < len(lines) and lines[j+1].isdigit() else None
                if lines[j] == 'Confirmation': card['confirmation_code'] = lines[j+1] if j+1 < len(lines) else None
            if card.get('confirmation_code'):
                reservations.append(card)
        i += 1
    print(f"  Found {len(reservations)} {tab} reservations")
    return reservations

def _clear_caesars_offer_filter(driver):
    """Remove the location filter on the offers page if one is active."""
    try:
        if 'Filters Applied' not in driver.find_element(By.TAG_NAME, 'body').text:
            return
        print("  🔍 Location filter detected — clearing...")
        driver.find_element(By.CSS_SELECTOR, '[aria-label="Open filter menu"]').click()
        human_delay(1.5, 2.5)
        for btn in driver.find_elements(By.CSS_SELECTOR, '[role="button"], button'):
            if btn.text.strip().upper() in ('CLEAR', 'CLEAR ALL', 'RESET', 'CLEAR FILTERS'):
                btn.click()
                human_delay(1, 2)
                break
        for btn in driver.find_elements(By.CSS_SELECTOR, '[role="button"], button'):
            if btn.text.strip().upper() in ('APPLY', 'DONE', 'SAVE'):
                btn.click()
                human_delay(1.5, 2.5)
                break
    except Exception as e:
        print(f"  ⚠️ Could not clear filter: {e}")

def scrape_caesars_offers(driver):
    print("🎁 Scraping offers...")
    driver.get('https://www.caesars.com/rewards/offers')
    human_delay(4, 6)
    _clear_caesars_offer_filter(driver)
    human_delay(2, 3)

    for _ in range(5):
        driver.execute_script("window.scrollBy(0, 800);")
        human_delay(0.8, 1.2)
    driver.execute_script("window.scrollTo(0, 0);")
    human_delay(1, 2)

    section_count = len(driver.find_elements(By.CSS_SELECTOR, '[data-testid="offer-group-see-more-button"]'))
    print(f"  Found {section_count} sections")

    all_offers = []

    for idx in range(section_count):
        driver.get('https://www.caesars.com/rewards/offers')
        human_delay(3, 5)
        for _ in range(5):
            driver.execute_script("window.scrollBy(0, 600);")
            human_delay(0.5, 1)
        driver.execute_script("window.scrollTo(0, 0);")
        human_delay(1, 2)

        see_more = driver.find_elements(By.CSS_SELECTOR, '[data-testid="offer-group-see-more-button"]')
        section_titles = driver.find_elements(By.CSS_SELECTOR, '[data-testid="offer-group-section-title"]')
        if idx >= len(see_more):
            break

        raw_name = section_titles[idx].text.strip() if idx < len(section_titles) else f'Section {idx+1}'
        name = re.sub(r'\s*\(\d+\)\s*$', '', raw_name).strip()
        print(f"  📂 Section: {name}...")

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", see_more[idx])
        human_delay(0.5, 1)
        see_more[idx].click()
        human_delay(3, 5)

        for _ in range(5):
            if driver.execute_script("return document.querySelectorAll('[aria-label=\"Open Offer Details\"]').length") > 0:
                break
            human_delay(2, 3)

        first_card = driver.find_elements(By.CSS_SELECTOR, '[aria-label="Open Offer Details"]')
        if not first_card:
            print(f"    No offer cards found")
            continue

        first_card[0].click()
        human_delay(1.5, 2.5)

        offer_count = 0
        seen_offer_ids = set()

        while offer_count < 150:
            offer = driver.execute_script("""
                var closeBtn = null;
                var allEls = document.querySelectorAll('[role="button"]');
                for (var i = 0; i < allEls.length; i++) {
                    if (allEls[i].innerText.trim() === 'Close') { closeBtn = allEls[i]; break; }
                }
                if (!closeBtn) return null;

                var overlay = closeBtn;
                for (var j = 0; j < 15; j++) {
                    overlay = overlay.parentElement;
                    if (overlay && overlay.offsetHeight > 800) break;
                }

                var lines = overlay.innerText
                    .split('\\n')
                    .map(function(l){ return l.trim(); })
                    .filter(function(l){ return l; });

                var title = '', dates = '', offerId = '', description = '';
                var properties = [];
                var inProperties = false;

                for (var k = 0; k < lines.length; k++) {
                    if (lines[k].match(/^Offer\\s+[A-Z0-9]/)) {
                        offerId = lines[k].replace(/^Offer\\s+/, '').trim();
                    }
                    if (lines[k].match(/^(Expires?|Valid)\\s/i)) { dates = lines[k]; }
                    if (lines[k] === 'AVAILABLE HOTELS & RESORTS') { inProperties = true; continue; }
                    if (lines[k] === 'HOW TO REDEEM:') { inProperties = false; continue; }
                    if (inProperties && lines[k].length > 2) { properties.push(lines[k]); }
                }

                var skipPat = /^(Close|EXPIRING|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|ADD TO MY CALENDAR)/i;
                for (var m = 0; m < Math.min(lines.length, 8); m++) {
                    if (lines[m].length > 2 && !skipPat.test(lines[m]) &&
                        lines[m] !== dates && !lines[m].match(/^Offer\\s/)) {
                        title = lines[m]; break;
                    }
                }

                for (var n = 0; n < lines.length; n++) {
                    if (lines[n] === 'ADD TO MY CALENDAR' && n + 1 < lines.length) {
                        description = lines[n + 1]; break;
                    }
                }

                return { title: title, offerId: offerId, dates: dates,
                         description: description, properties: properties.join(', ') };
            """)

            if not offer or not offer.get('offerId'):
                break
            oid = offer['offerId']
            if oid in seen_offer_ids:
                break
            seen_offer_ids.add(oid)
            offer['section'] = name
            offer['offer_id'] = oid
            all_offers.append(offer)
            offer_count += 1

            next_btn = driver.find_elements(By.CSS_SELECTOR, '[aria-label*="NEXT offer"]')
            if not next_btn:
                break
            next_btn[0].click()
            human_delay(0.6, 1.2)

        try:
            for cb in driver.find_elements(By.CSS_SELECTOR, '[role="button"]'):
                if cb.text.strip() == 'Close':
                    cb.click()
                    break
        except Exception:
            pass
        human_delay(1, 2)
        print(f"    {offer_count} offers scraped")

    seen = set()
    unique_offers = []
    for o in all_offers:
        oid = o.get('offer_id', '')
        if oid and oid not in seen:
            seen.add(oid)
            unique_offers.append(o)

    print(f"  Found {len(unique_offers)} total unique offers")
    return unique_offers

def scrape_caesars_great_gift(driver):
    print("🎄 Scraping Great Gift...")
    try:
        driver.get('https://www.caesars.com/myrewards/promotions/ggwu-points')
        human_delay(4, 6)
        wait = WebDriverWait(driver, 15)
        handles_before = set(driver.window_handles)
        shop_clicked = False
        try:
            shop_el = wait.until(EC.element_to_be_clickable(
                (By.XPATH, '//*[@id="uuid-5d898d29-470d-4d8f-8e91-ef838ad0cf2c"]/div/div/div/a')
            ))
            human_click(driver, shop_el)
            shop_clicked = True
        except TimeoutException:
            print("  XPath not found, trying text/link search...")
        if not shop_clicked:
            for link in driver.find_elements(By.TAG_NAME, 'a'):
                try:
                    if ('Shop Now' in link.text or 'SHOP NOW' in link.text) and link.is_displayed():
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
                        human_delay(0.5, 1)
                        human_click(driver, link)
                        shop_clicked = True
                        break
                except Exception:
                    continue
        if not shop_clicked:
            driver.execute_script("""
                var links = document.querySelectorAll('a');
                for (var i = 0; i < links.length; i++) {
                    if (links[i].textContent.trim().toUpperCase().includes('SHOP NOW')) {
                        links[i].click(); break;
                    }
                }
            """)
        human_delay(5, 8)

        new_handles = set(driver.window_handles) - handles_before
        if new_handles:
            driver.switch_to.window(new_handles.pop())
            human_delay(2, 3)

        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            if '/verification/step-up' in driver.current_url:
                handle_caesars_2fa(driver)
                human_delay(5, 8)
                break

        human_delay(3, 5)
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            if 'incentiveusa' in driver.current_url or 'propelhq' in driver.current_url:
                break

        text = driver.find_element(By.TAG_NAME, 'body').text
        m = re.search(r'Great Gift Points Balance:\s*([\d,]+)', text, re.I)
        points = int(m.group(1).replace(',', '')) if m else None
        if points is None:
            print(f"  Page text preview: {text[:300]}")
        print(f"  ✅ Great Gift Points: {points}")

        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(driver.window_handles[0])

        return points
    except Exception as e:
        print(f"  ⚠️ Could not scrape Great Gift: {e}")
        import traceback; traceback.print_exc()
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[0])
        return None

# ── Save to Supabase ──────────────────────────────────────────────────────────
def save_caesars_snapshot(data):
    supabase.table('caesars_rewards_snapshots').insert({
        'run_ts': RUN_TS,
        'reward_credits': data.get('reward_credits'),
        'tier_credits': data.get('tier_credits'),
        'tier_status': data.get('tier_status'),
        'tier_next': data.get('tier_next'),
        'tier_credits_needed': data.get('tier_credits_needed'),
        'last_earned_date': parse_date(data.get('last_earned_date')),
        'credits_expire_date': parse_date(data.get('credits_expire_date')),
        'great_gift_points': data.get('great_gift_points'),
    }).execute()
    print("  💾 Saved Caesars snapshot")

def save_caesars_reservations(reservations):
    for r in reservations:
        if not r.get('confirmation_code'):
            continue
        supabase.table('caesars_reservations').upsert({
            'run_ts': RUN_TS,
            'confirmation_code': r['confirmation_code'],
            'property': r.get('property'),
            'location': r.get('location'),
            'check_in': parse_date(r.get('check_in')),
            'check_out': parse_date(r.get('check_out')),
            'adults': r.get('adults'),
            'children': r.get('children'),
            'status': 'Active',
            'tab': r.get('tab'),
            'updated_at': datetime.now().isoformat(),
        }, on_conflict='confirmation_code').execute()
    print(f"  💾 Saved {len(reservations)} reservations")

def save_caesars_offers(offers):
    saved = 0
    for o in offers:
        if not o.get('offer_id') or not o.get('title'):
            continue
        valid_start, valid_end, expires_at = parse_caesars_dates(o.get('dates'))
        supabase.table('caesars_offers').upsert({
            'offer_id': o['offer_id'],
            'title': o['title'],
            'description': o.get('description') or None,
            'section': o.get('section'),
            'eligible_properties': o.get('properties') or None,
            'valid_start': valid_start,
            'valid_end': valid_end,
            'expires_at': expires_at,
            'last_seen': datetime.now().isoformat(),
        }, on_conflict='offer_id').execute()
        saved += 1
    print(f"  💾 Saved {saved} offers")

# ── Browser setup ─────────────────────────────────────────────────────────────
def make_driver(visible=False):
    options = uc.ChromeOptions()
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--lang=en-US')
    if visible:
        options.add_argument('--start-maximized')
    # DO NOT use --headless — Imperva detects it
    # DO NOT pass version_main — uc matches ChromeDriver to the binary it actually launches;
    # manual detection can point to a different Chrome binary and cause a version mismatch.
    # Use xvfb on Linux CI for a virtual display instead.
    return uc.Chrome(options=options, use_subprocess=True)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--visible', action='store_true', help='Open a visible Chrome window')
    args = parser.parse_args()
    visible = args.visible or bool(os.environ.get('LOCAL_DEBUG'))

    start = time.time()
    print("🎰 Caesars Rewards Scraper")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} PT")
    if visible:
        print("   👁  Visible mode\n")

    driver = make_driver(visible)
    exit_code = 0
    try:
        caesars_login(driver)
        reservations = scrape_caesars_reservations(driver, 'past')
        reservations += scrape_caesars_reservations(driver, 'current')
        offers = scrape_caesars_offers(driver)

        rewards = scrape_caesars_rewards(driver)
        great_gift = scrape_caesars_great_gift(driver)
        rewards['great_gift_points'] = great_gift

        # If all key snapshot values are null the page probably didn't load correctly.
        # Wait 30 minutes and try once more before giving up.
        key_vals = [rewards.get('reward_credits'), rewards.get('tier_credits'), rewards.get('great_gift_points')]
        if all(v is None for v in key_vals):
            print("\n⚠️  reward_credits, tier_credits, and great_gift_points are all null.")
            print("    Waiting 30 minutes then retrying snapshot scrape...")
            time.sleep(30 * 60)
            rewards = scrape_caesars_rewards(driver)
            great_gift = scrape_caesars_great_gift(driver)
            rewards['great_gift_points'] = great_gift
            key_vals = [rewards.get('reward_credits'), rewards.get('tier_credits'), rewards.get('great_gift_points')]
            if all(v is None for v in key_vals):
                print("❌ Still null after retry — saving anyway and exiting with code 2")
                exit_code = 2

        save_caesars_snapshot(rewards)
        save_caesars_reservations(reservations)
        save_caesars_offers(offers)
        if exit_code == 0:
            print("\n✅ Caesars scrape complete!")
    except Exception as e:
        import traceback
        print(f"\n❌ Caesars scrape failed: {e}")
        traceback.print_exc()
        exit_code = 1
    finally:
        driver.quit()
        print(f"🏁 Done in {time.time() - start:.1f}s")

    sys.exit(exit_code)

if __name__ == '__main__':
    main()
