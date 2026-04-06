#!/usr/bin/env python3
"""Minimal Caesars login test — bare undetected-chromedriver, no extra flags."""

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

# Bare minimum options — let undetected-chromedriver handle everything
options = uc.ChromeOptions()
options.add_argument('--window-size=1920,1080')

# On Linux CI, use xvfb for a virtual display instead of headless
# DO NOT use --headless — it's detectable by Imperva

os.makedirs('debug', exist_ok=True)

if chrome_ver:
    driver = uc.Chrome(options=options, use_subprocess=True, version_main=chrome_ver)
else:
    driver = uc.Chrome(options=options, use_subprocess=True)

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
    except:
        print(f"  ❌ userID input NOT found")
        text = driver.find_element(By.TAG_NAME, 'body').text[:500]
        print(f"  Page text: {text[:200]}")
        driver.save_screenshot('debug/02-no-input.png')
        raise Exception("Login form did not load")

    # Step 3: Type credentials with human-like behavior
    print("Step 3: Entering credentials...")
    user_input.click()
    time.sleep(random.uniform(0.3, 0.7))
    user_input.clear()
    for char in os.environ['CAESARS_USERNAME']:
        user_input.send_keys(char)
        time.sleep(random.uniform(0.05, 0.15))
    time.sleep(random.uniform(0.5, 1.0))

    # React value setter
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
    time.sleep(random.uniform(0.3, 0.7))
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

    # Wait for page to load — give it extra time
    time.sleep(10)
    driver.save_screenshot('debug/04-after-signin.png')
    print(f"  URL: {driver.current_url}")

    # Wait a bit more in case of redirect
    time.sleep(5)
    driver.save_screenshot('debug/05-settled.png')
    print(f"  URL: {driver.current_url}")

    # Check result
    text = driver.find_element(By.TAG_NAME, 'body').text[:500]
    print(f"  Page text: {text[:300]}")

    if 'REWARD CREDITS' in text or 'Reward Credits' in text:
        print("\n🎉 LOGIN SUCCESS — Rewards page loaded!")
    elif 'incapsula' in text.lower() or 'security check' in text.lower():
        print("\n❌ IMPERVA CHALLENGE — bot detected")
    elif 'blocked' in text.lower() or 'access denied' in text.lower():
        print("\n❌ BLOCKED by WAF")
    elif 'signin' in driver.current_url.lower():
        print("\n⚠️ Still on signin page")
    else:
        print(f"\n❓ Unknown state")

    driver.save_screenshot('debug/06-final.png')

except Exception as e:
    print(f"\n💥 Error: {e}")
    driver.save_screenshot('debug/error.png')
finally:
    driver.quit()
    print("Done.")
