#!/usr/bin/env python3
"""Minimal Caesars login test — login and screenshot."""

import os, re, time, random, json, platform, subprocess
from dotenv import load_dotenv
load_dotenv()

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Detect Chrome version
chrome_ver = None
try:
    if platform.system() == 'Darwin':
        result = subprocess.run(
            ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--version'],
            capture_output=True, text=True)
    else:
        result = subprocess.run(['google-chrome', '--version'], capture_output=True, text=True)
    ver_match = re.search(r'(\d+)\.', result.stdout)
    chrome_ver = int(ver_match.group(1)) if ver_match else None
except:
    pass

print(f"Chrome version: {chrome_ver or 'auto'}")
print(f"CI: {os.environ.get('CI', 'false')}")

options = uc.ChromeOptions()
options.add_argument('--window-size=1920,1080')
options.add_argument('--lang=en-US')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
if os.environ.get('CI'):
    options.add_argument('--headless=new')

driver = uc.Chrome(options=options, use_subprocess=True,
                   version_main=chrome_ver) if chrome_ver else uc.Chrome(options=options, use_subprocess=True)

try:
    # Step 1: Navigate to login page
    print("Step 1: Loading signin page...")
    driver.get('https://www.caesars.com/myrewards/profile/signin/')
    time.sleep(5)
    driver.save_screenshot('debug/01-signin-loaded.png')
    print(f"  URL: {driver.current_url}")
    print(f"  Title: {driver.title}")

    # Step 2: Wait for the userID input
    print("Step 2: Waiting for userID input (10s)...")
    try:
        user_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "userID"))
        )
        print(f"  ✅ Found userID input")
        driver.save_screenshot('debug/02-input-found.png')
    except:
        print(f"  ❌ userID input NOT found after 10s")
        # Dump what IS on the page
        text = driver.find_element(By.TAG_NAME, 'body').text[:500]
        print(f"  Page text: {text}")
        driver.save_screenshot('debug/02-input-not-found.png')

        # Check for bot challenge
        html = driver.page_source[:2000]
        if 'imperva' in html.lower() or 'incapsula' in html.lower():
            print("  ⚠️ IMPERVA/INCAPSULA detected in page source")
        if 'challenge' in html.lower():
            print("  ⚠️ Challenge detected in page source")
        if 'blocked' in html.lower():
            print("  ⚠️ Blocked detected in page source")
        if 'captcha' in html.lower():
            print("  ⚠️ CAPTCHA detected in page source")

        # Save HTML for analysis
        os.makedirs('debug', exist_ok=True)
        with open('debug/02-page-source.html', 'w') as f:
            f.write(driver.page_source)

        raise Exception("Login form did not load")

    # Step 3: Type credentials
    print("Step 3: Entering credentials...")
    user_input.click()
    time.sleep(0.5)
    user_input.clear()
    for char in os.environ['CAESARS_USERNAME']:
        user_input.send_keys(char)
        time.sleep(random.uniform(0.05, 0.15))
    time.sleep(1)

    # Use React-compatible value setter
    driver.execute_script("""
        var input = arguments[0];
        var nativeSetter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        nativeSetter.call(input, arguments[1]);
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
    """, user_input, os.environ['CAESARS_USERNAME'])

    pass_input = driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
    pass_input.click()
    time.sleep(0.5)
    for char in os.environ['CAESARS_PASSWORD']:
        pass_input.send_keys(char)
        time.sleep(random.uniform(0.05, 0.15))

    driver.execute_script("""
        var input = arguments[0];
        var nativeSetter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        nativeSetter.call(input, arguments[1]);
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
    """, pass_input, os.environ['CAESARS_PASSWORD'])

    driver.save_screenshot('debug/03-creds-entered.png')
    print("  ✅ Credentials entered")

    # Step 4: Click Sign In
    print("Step 4: Clicking Sign In...")
    buttons = driver.find_elements(By.TAG_NAME, 'button')
    for btn in buttons:
        if btn.text.strip() in ('SIGN IN', 'Sign In') and btn.is_displayed():
            btn.click()
            print("  ✅ Clicked Sign In")
            break

    time.sleep(8)
    driver.save_screenshot('debug/04-after-signin.png')
    print(f"  URL after login: {driver.current_url}")
    print(f"  Title: {driver.title}")

    # Step 5: Check result
    text = driver.find_element(By.TAG_NAME, 'body').text[:500]
    print(f"  Page text: {text[:200]}")

    if 'REWARD CREDITS' in text:
        print("\n🎉 LOGIN SUCCESS — Rewards page loaded!")
    elif 'blocked' in text.lower() or 'access denied' in text.lower():
        print("\n❌ BLOCKED by WAF")
    elif 'signin' in driver.current_url.lower():
        print("\n⚠️ Still on signin page — login may have failed")
    else:
        print(f"\n❓ Unknown state — URL: {driver.current_url}")

    driver.save_screenshot('debug/05-final.png')

except Exception as e:
    print(f"\n💥 Error: {e}")
    os.makedirs('debug', exist_ok=True)
    driver.save_screenshot('debug/error.png')
finally:
    driver.quit()
    print("\nDone.")
