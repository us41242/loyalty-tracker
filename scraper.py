#!/usr/bin/env python3
"""
Casino Loyalty Tracker — Daily scraper for Caesars, Rio, and MGM
Uses undetected-chromedriver to bypass bot detection (Imperva, etc.)
"""

import os
import re
import time
import random
import json
from datetime import datetime, date

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

# ── Config ───────────────────────────────────────────────────────────────────
supabase = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

# ── Cookie Session Management ────────────────────────────────────────────────
def save_cookies(driver, site):
    """Save browser cookies to Supabase for reuse across runs."""
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
    """Load cookies from Supabase and inject into browser. Returns True if cookies loaded."""
    try:
        result = supabase.table('session_cookies').select('cookies, updated_at').eq('site', site).execute()
        if not result.data or len(result.data) == 0:
            print(f"  🍪 No saved cookies for {site}")
            return False

        row = result.data[0]
        cookies = json.loads(row['cookies'])
        updated = row['updated_at']
        print(f"  🍪 Loading {len(cookies)} cookies for {site} (saved: {updated[:19]})")

        # Navigate to the domain first so cookies can be set
        driver.get(f'https://{domain}/')
        human_delay(2, 3)

        for cookie in cookies:
            # Remove problematic fields
            for key in ['sameSite', 'expiry', 'httpOnly', 'storeId']:
                cookie.pop(key, None)
            # Ensure domain matches
            if 'domain' in cookie and domain not in cookie['domain']:
                continue
            try:
                driver.add_cookie(cookie)
            except:
                pass

        return True
    except Exception as e:
        print(f"  🍪 Could not load cookies for {site}: {e}")
        return False

def verify_session(driver, check_url, success_indicator):
    """Navigate to a page and check if we're logged in."""
    driver.get(check_url)
    human_delay(3, 5)
    text = driver.find_element(By.TAG_NAME, 'body').text
    return success_indicator.lower() in text.lower()

# ── Human-like helpers ───────────────────────────────────────────────────────
def human_delay(min_s=1.0, max_s=3.0):
    time.sleep(random.uniform(min_s, max_s))

def human_type(element, text, min_delay=0.05, max_delay=0.15):
    """Type text character by character with human-like delays."""
    element.click()
    human_delay(0.3, 0.7)
    element.clear()
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(min_delay, max_delay))
    human_delay(0.3, 0.8)

def human_click(driver, element):
    """Click with slight random offset via ActionChains."""
    from selenium.webdriver.common.action_chains import ActionChains
    actions = ActionChains(driver)
    # Move to element with slight offset
    x_off = random.randint(-3, 3)
    y_off = random.randint(-3, 3)
    actions.move_to_element_with_offset(element, x_off, y_off)
    human_delay(0.1, 0.3)
    actions.click()
    actions.perform()
    human_delay(0.5, 1.5)

def parse_date(date_str):
    """Parse various date formats to YYYY-MM-DD."""
    if not date_str:
        return None
    # MM/DD/YYYY
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', date_str)
    if m:
        return f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    # Mon DD, YYYY
    months = {'Jan':'01','Feb':'02','Mar':'03','Apr':'04','May':'05','Jun':'06',
              'Jul':'07','Aug':'08','Sep':'09','Oct':'10','Nov':'11','Dec':'12'}
    m = re.match(r'([A-Z][a-z]{2})\s+(\d{1,2}),?\s*(\d{4})', date_str)
    if m and m.group(1) in months:
        return f"{m.group(3)}-{months[m.group(1)]}-{m.group(2).zfill(2)}"
    return date_str

_last_used_mfa_message_id = None

def _prime_last_mfa_id():
    """Record the current latest MFA email ID so we skip it when polling for a new one."""
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
    """Fetch 2FA code from Gmail via OAuth2.
    Tracks the last used message ID to avoid reusing stale codes.
    """
    global _last_used_mfa_message_id
    import urllib.request
    import urllib.parse

    client_id = os.environ.get('GMAIL_CLIENT_ID')
    client_secret = os.environ.get('GMAIL_CLIENT_SECRET')
    refresh_token = os.environ.get('GMAIL_REFRESH_TOKEN')

    if not all([client_id, client_secret, refresh_token]):
        return input("  Enter 2FA code from email: ").strip()

    # Get access token
    data = urllib.parse.urlencode({
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
    }).encode()
    req = urllib.request.Request('https://oauth2.googleapis.com/token', data=data)
    resp = json.loads(urllib.request.urlopen(req).read())
    access_token = resp.get('access_token')
    if not access_token:
        print("  ❌ Could not refresh Gmail token")
        return input("  Enter 2FA code from email: ").strip()

    # Poll for MFA email
    print("  ⏳ Polling Gmail for MFA code...")
    for attempt in range(24):  # 2 minutes max
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

                # Skip if this is the same message we already used
                if msg_id == _last_used_mfa_message_id:
                    print(f"  Attempt {attempt+1}/24 - same code as before, waiting for new one...")
                    continue

                msg_url = f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=full'
                req2 = urllib.request.Request(msg_url)
                req2.add_header('Authorization', f'Bearer {access_token}')
                msg = json.loads(urllib.request.urlopen(req2).read())

                snippet = msg.get('snippet', '')
                code_match = re.search(r'\b(\d{6})\b', snippet)
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


# ═══════════════════════════════════════════════════════════════════════════════
#   CAESARS
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_caesars(driver):
    print("\n═══════════════════════════════════════")
    print("  CAESARS REWARDS SCRAPER")
    print("═══════════════════════════════════════\n")

    try:
        caesars_login(driver)
        rewards = scrape_caesars_rewards(driver)
        reservations = scrape_caesars_reservations(driver, 'past')
        reservations += scrape_caesars_reservations(driver, 'current')
        offers = scrape_caesars_offers(driver)
        great_gift = scrape_caesars_great_gift(driver)
        rewards['great_gift_points'] = great_gift

        save_caesars_snapshot(rewards)
        save_caesars_reservations(reservations)
        save_caesars_offers(offers)
        print("\n✅ Caesars scrape complete!")
    except Exception as e:
        print(f"❌ Caesars error: {e}")

def caesars_login(driver):
    print("🔑 Logging in to Caesars...")

    # Try loading saved cookies first
    if load_cookies(driver, 'caesars', 'www.caesars.com'):
        if verify_session(driver, 'https://www.caesars.com/rewards/home', 'REWARD CREDITS'):
            print("  ✅ Session restored from cookies!")
            return
        else:
            print("  🍪 Cookies expired, doing full login...")

    driver.get('https://www.caesars.com/myrewards/profile/signin/')
    human_delay(3, 5)

    wait = WebDriverWait(driver, 20)

    # Username
    try:
        user_input = wait.until(EC.presence_of_element_located((By.NAME, "userID")))
        human_type(user_input, os.environ['CAESARS_USERNAME'])
        print("  Username entered")
    except:
        # Fallback: find any visible text input
        inputs = driver.find_elements(By.TAG_NAME, 'input')
        for inp in inputs:
            if inp.get_attribute('type') in ('text', 'email', None) and inp.is_displayed():
                human_type(inp, os.environ['CAESARS_USERNAME'])
                print("  Username entered (fallback)")
                break

    human_delay(0.8, 1.5)

    # Password
    pass_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="password"]')))
    human_type(pass_input, os.environ['CAESARS_PASSWORD'])
    print("  Password entered")
    human_delay(1, 2)

    # Click SIGN IN
    buttons = driver.find_elements(By.TAG_NAME, 'button')
    for btn in buttons:
        if btn.text.strip() in ('SIGN IN', 'Sign In') and btn.is_displayed():
            human_click(driver, btn)
            print("  Clicked Sign In")
            break

    human_delay(5, 8)
    print(f"  URL after login: {driver.current_url}")

    # Handle 2FA if needed
    if '/verification/step-up' in driver.current_url:
        handle_caesars_2fa(driver)

    # Save cookies after successful login
    save_cookies(driver, 'caesars')

def handle_caesars_2fa(driver):
    print("🔐 2FA required...")

    # Before waiting for the new code, record the current latest MFA message
    # so get_2fa_code() will skip it and wait for a fresh one
    _prime_last_mfa_id()

    human_delay(8, 12)

    code = get_2fa_code()
    if not code:
        raise Exception("Could not get 2FA code")

    print(f"  Entering code: {code}")

    # Click into the first input field using the specific XPath
    try:
        first_input_xpath = '//*[@id="root"]/div/div/div[1]/div/div/div[2]/div[1]/div[4]/div[2]/div[1]/div/input[1]'
        first_input = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, first_input_xpath))
        )
        first_input.click()
        human_delay(0.3, 0.6)
    except:
        # Fallback: click the first maxlength=1 input
        inputs = driver.find_elements(By.CSS_SELECTOR, 'input[maxlength="1"]')
        if inputs:
            inputs[0].click()
            human_delay(0.3, 0.6)

    # Type all 6 digits — use keyboard actions after clicking first input
    from selenium.webdriver.common.action_chains import ActionChains
    actions = ActionChains(driver)
    for digit in code:
        actions.send_keys(digit)
        actions.pause(random.uniform(0.15, 0.35))
    actions.perform()
    human_delay(1, 2)

    # Auto-submit happens after 6th digit — wait for redirect
    print("  Waiting for verification...")
    for _ in range(20):
        human_delay(2, 3)
        if '/verification/step-up' not in driver.current_url:
            break

    print(f"  ✅ 2FA complete, URL: {driver.current_url}")

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

    # Click tab
    try:
        tab_links = driver.find_elements(By.CSS_SELECTOR, 'a, button, span')
        for link in tab_links:
            if link.text.strip().upper() == tab.upper() and link.is_displayed():
                link.click()
                human_delay(2, 3)
                break
    except:
        pass

    text = driver.find_element(By.TAG_NAME, 'body').text
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    reservations = []
    i = 0
    while i < len(lines):
        if lines[i] == 'Property':
            card = {'tab': tab}
            for j in range(i, min(i + 20, len(lines))):
                if lines[j] == 'Property': card['property'] = lines[j+1] if j+1 < len(lines) else None
                if lines[j] == 'Location': card['location'] = lines[j+1] if j+1 < len(lines) else None
                if lines[j] == 'Check-In': card['check_in'] = lines[j+1] if j+1 < len(lines) else None
                if lines[j] == 'Checkout': card['check_out'] = lines[j+1] if j+1 < len(lines) else None
                if lines[j] == 'Adults': card['adults'] = int(lines[j+1]) if j+1 < len(lines) and lines[j+1].isdigit() else None
                if lines[j] == 'Children': card['children'] = int(lines[j+1]) if j+1 < len(lines) and lines[j+1].isdigit() else None
                if lines[j] == 'Confirmation': card['confirmation_code'] = lines[j+1] if j+1 < len(lines) else None
            if card.get('confirmation_code'):
                reservations.append(card)
        i += 1

    print(f"  Found {len(reservations)} {tab} reservations")
    return reservations

def scrape_caesars_offers(driver):
    print("🎁 Scraping offers...")
    driver.get('https://www.caesars.com/rewards/offers')
    human_delay(3, 5)

    # Clear filters
    try:
        buttons = driver.find_elements(By.CSS_SELECTOR, 'button, a')
        for btn in buttons:
            if btn.text.strip() == 'Clear Filters' and btn.is_displayed():
                btn.click()
                human_delay(2, 3)
                break
    except:
        pass

    # Click See More buttons
    for _ in range(10):
        try:
            see_more = [b for b in driver.find_elements(By.CSS_SELECTOR, 'button, a')
                       if 'See More' in b.text and b.is_displayed()]
            if see_more:
                see_more[0].click()
                human_delay(1.5, 3)
            else:
                break
        except:
            break

    text = driver.find_element(By.TAG_NAME, 'body').text
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    offers = []
    current_section = 'Unknown'

    for i, line in enumerate(lines):
        sm = re.match(r'^(EXPIRING.*?|(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+OFFERS?)\s*\(\d+\)', line, re.I)
        if sm:
            current_section = sm.group(1).strip()
            continue

        if re.match(r'^Expires?\s+(today|tomorrow|\d)', line, re.I) or re.match(r'^Valid\s+\d', line, re.I):
            title = ''
            for j in range(i-1, max(0, i-4), -1):
                if not re.match(r'^(EXPIRING|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)', lines[j], re.I):
                    if not re.match(r'^(See More|Clear Filters|FILTER|DESTINATIONS)', lines[j], re.I):
                        title = lines[j]
                        break
            if title:
                offers.append({
                    'title': title,
                    'section': current_section,
                    'dates': line,
                    'offer_id': f"{title}-{line}".replace(' ', '-')[:50],
                })

    print(f"  Found {len(offers)} offers")
    return offers

def scrape_caesars_great_gift(driver):
    print("🎄 Scraping Great Gift...")
    try:
        # Step 1: Navigate directly to the Great Gift Wrap Up page
        print("  Step 1: Navigating to Great Gift Wrap Up page...")
        driver.get('https://www.caesars.com/myrewards/promotions/ggwu-points')
        human_delay(4, 6)
        print(f"  Step 1 done, URL: {driver.current_url}")
        wait = WebDriverWait(driver, 15)

        # Step 3: Click Shop Now
        print("  Step 3: Clicking Shop Now...")
        # Remember current window handles before click
        handles_before = set(driver.window_handles)

        # Try XPath first, then text search
        shop_clicked = False
        try:
            shop_xpath = '//*[@id="uuid-5d898d29-470d-4d8f-8e91-ef838ad0cf2c"]/div/div/div/a'
            shop_el = wait.until(EC.element_to_be_clickable((By.XPATH, shop_xpath)))
            human_click(driver, shop_el)
            shop_clicked = True
        except TimeoutException:
            print("  XPath not found, trying text/link search...")

        if not shop_clicked:
            # Try finding by link text
            links = driver.find_elements(By.TAG_NAME, 'a')
            for link in links:
                try:
                    text = link.text.strip()
                    href = link.get_attribute('href') or ''
                    if ('Shop Now' in text or 'SHOP NOW' in text) and link.is_displayed():
                        # Scroll to element first
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
                        human_delay(0.5, 1)
                        human_click(driver, link)
                        shop_clicked = True
                        print(f"  Clicked Shop Now link (href: {href[:80]})")
                        break
                except:
                    continue

        if not shop_clicked:
            # Last resort: click via JS
            driver.execute_script("""
                var links = document.querySelectorAll('a');
                for (var i = 0; i < links.length; i++) {
                    if (links[i].textContent.trim().toUpperCase().includes('SHOP NOW')) {
                        links[i].click();
                        break;
                    }
                }
            """)
            print("  Clicked Shop Now via JS")

        human_delay(5, 8)

        # Check for new tab
        handles_after = set(driver.window_handles)
        new_handles = handles_after - handles_before
        if new_handles:
            new_tab = new_handles.pop()
            driver.switch_to.window(new_tab)
            human_delay(2, 3)
            print(f"  Switched to new tab, URL: {driver.current_url}")

        print(f"  Step 3 done, URL: {driver.current_url}")

        # Step 4: Handle 2FA if triggered
        # Check all tabs for 2FA page
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            if '/verification/step-up' in driver.current_url:
                print("  Step 4: 2FA triggered...")
                handle_caesars_2fa(driver)
                human_delay(5, 8)
                print(f"  After 2FA, URL: {driver.current_url}")
                break

        # Step 5: Find the Great Gift page (propelhq.incentiveusa.com)
        human_delay(3, 5)

        # Check all tabs for the incentive page
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            if 'incentiveusa' in driver.current_url or 'propelhq' in driver.current_url:
                print(f"  Found Great Gift page: {driver.current_url}")
                break

        text = driver.find_element(By.TAG_NAME, 'body').text
        m = re.search(r'Great Gift Points Balance:\s*([\d,]+)', text, re.I)
        points = int(m.group(1).replace(',', '')) if m else None

        if points is None:
            # Debug: print first 500 chars of page
            print(f"  Page text preview: {text[:300]}")

        print(f"  ✅ Great Gift Points: {points}")

        # Close extra tab if opened, switch back to main
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(driver.window_handles[0])

        return points
    except Exception as e:
        print(f"  ⚠️ Could not scrape Great Gift: {e}")
        import traceback
        traceback.print_exc()
        # Make sure we're on the main window
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[0])
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#   RIO
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_rio(driver):
    print("\n═══════════════════════════════════════")
    print("  RIO REWARDS SCRAPER")
    print("═══════════════════════════════════════\n")

    try:
        rio_login(driver)
        data = scrape_rio_rewards(driver)
        save_rio_snapshot(data['snapshot'])
        save_rio_offers(data['offers'])
        print("\n✅ Rio scrape complete!")
    except Exception as e:
        print(f"❌ Rio error: {e}")

def rio_login(driver):
    print("🔑 Logging in to Rio...")

    # Try loading saved cookies first
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
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="email"], input[type="email"], input[name="username"]'))
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

def scrape_rio_rewards(driver):
    if '/rio-rewards/offers' not in driver.current_url:
        driver.get('https://www.riolasvegas.com/rio-rewards/offers')
        human_delay(3, 5)

    print("📊 Scraping Rio rewards and offers...")
    text = driver.find_element(By.TAG_NAME, 'body').text

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

    # Parse offers
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


# ═══════════════════════════════════════════════════════════════════════════════
#   MGM
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_mgm(driver):
    print("\n═══════════════════════════════════════")
    print("  MGM REWARDS SCRAPER")
    print("═══════════════════════════════════════\n")

    try:
        mgm_login(driver)
        rewards = scrape_mgm_rewards(driver)
        trips = scrape_mgm_trips(driver)
        save_mgm_snapshot(rewards)
        save_mgm_trips(trips)
        print("\n✅ MGM scrape complete!")
    except Exception as e:
        print(f"❌ MGM error: {e}")

def mgm_login(driver):
    print("🔑 Logging in to MGM...")

    # Try loading saved cookies first
    if load_cookies(driver, 'mgm', 'www.mgmresorts.com'):
        if verify_session(driver, 'https://www.mgmresorts.com/rewards/', 'Tier Credits'):
            print("  ✅ Session restored from cookies!")
            return
        else:
            print("  🍪 Cookies expired, doing full login...")

    # Visit main site first to build cookies
    driver.get('https://www.mgmresorts.com/')
    human_delay(3, 5)

    driver.get('https://www.mgmresorts.com/identity/?client_id=mgm_app_web&redirect_uri=https://www.mgmresorts.com/rewards/&scopes=')
    human_delay(3, 6)

    try:
        # Step 1: Email
        email_input = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, 'email'))
        )
        human_type(email_input, os.environ['MGM_EMAIL'])
        human_delay(1, 2)

        # Click Next
        next_btn = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        human_click(driver, next_btn)
        human_delay(3, 5)

        # Step 2: Password
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

    # Parse trips if they exist
    trips = []
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for line in lines:
        m = re.search(r'Confirmation[:\s#]+([A-Z0-9]+)', line, re.I)
        if m:
            trips.append({'confirmation_code': m.group(1)})

    print(f"  Found {len(trips)} trips")
    return trips


# ═══════════════════════════════════════════════════════════════════════════════
#   SAVE TO SUPABASE
# ═══════════════════════════════════════════════════════════════════════════════

def save_caesars_snapshot(data):
    result = supabase.table('caesars_rewards_snapshots').insert({
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
        supabase.table('caesars_offers').upsert({
            'offer_id': o['offer_id'],
            'title': o['title'],
            'section': o.get('section'),
            'last_seen': datetime.now().isoformat(),
        }, on_conflict='offer_id').execute()
        saved += 1
    print(f"  💾 Saved {saved} offers")

def save_rio_snapshot(data):
    supabase.table('rio_rewards_snapshots').insert({
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
    for o in offers:
        if not o.get('title'):
            continue
        parts = o.get('dates', '').split(' - ') if o.get('dates') else [None, None]
        valid_start = date.today().isoformat() if parts[0] == 'Now' else parse_date(parts[0]) if len(parts) > 0 else None
        valid_end = parse_date(parts[1]) if len(parts) > 1 else None
        try:
            supabase.table('rio_offers').upsert({
                'title': o['title'],
                'valid_start': valid_start,
                'valid_end': valid_end,
                'last_seen': datetime.now().isoformat(),
            }, on_conflict='title,valid_start,valid_end').execute()
            saved += 1
        except:
            pass
    print(f"  💾 Saved {saved} offers")

def save_mgm_snapshot(data):
    supabase.table('mgm_rewards_snapshots').insert({
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
            'confirmation_code': t['confirmation_code'],
            'tab': 'past',
            'updated_at': datetime.now().isoformat(),
        }, on_conflict='confirmation_code').execute()
    if trips:
        print(f"  💾 Saved {len(trips)} trips")


# ═══════════════════════════════════════════════════════════════════════════════
#   MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    start = time.time()
    print("🚀 Casino Loyalty Tracker (undetected-chromedriver)")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} PT\n")

    options = uc.ChromeOptions()
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--lang=en-US')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    if os.environ.get('CI'):
        options.add_argument('--headless=new')

    # Detect Chrome version automatically
    import subprocess, platform
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
        ver_match = re.search(r'(\d+)\.', result.stdout)
        chrome_ver = int(ver_match.group(1)) if ver_match else None
    except:
        pass

    print(f"  Chrome version: {chrome_ver or 'auto-detect'}")
    driver = uc.Chrome(options=options, use_subprocess=True,
                       version_main=chrome_ver) if chrome_ver else uc.Chrome(options=options, use_subprocess=True)

    try:
        scrape_caesars(driver)
        scrape_rio(driver)
        scrape_mgm(driver)
    except Exception as e:
        print(f"\n💥 Fatal error: {e}")
    finally:
        driver.quit()
        elapsed = time.time() - start
        print(f"\n🏁 Done in {elapsed:.1f}s")

if __name__ == '__main__':
    main()
