#!/usr/bin/env python3
"""Dump the offers page DOM to find the real section links."""

import os, re, time, platform, subprocess
from dotenv import load_dotenv
load_dotenv()

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

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

options = uc.ChromeOptions()
options.add_argument('--window-size=1920,1080')
if os.environ.get('CI'):
    pass  # no headless

os.makedirs('debug', exist_ok=True)

driver = uc.Chrome(options=options, use_subprocess=True,
                   version_main=chrome_ver) if chrome_ver else uc.Chrome(options=options, use_subprocess=True)

try:
    # Login
    print("Logging in...")
    driver.get('https://www.caesars.com/myrewards/profile/signin/')
    time.sleep(5)

    user_input = driver.find_element(By.NAME, "userID")
    user_input.click()
    time.sleep(0.3)
    user_input.clear()
    for char in os.environ['CAESARS_USERNAME']:
        user_input.send_keys(char)
        time.sleep(0.08)

    driver.execute_script("""
        var input = arguments[0];
        var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        nativeSetter.call(input, arguments[1]);
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
    """, user_input, os.environ['CAESARS_USERNAME'])

    pass_input = driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
    pass_input.click()
    time.sleep(0.3)
    for char in os.environ['CAESARS_PASSWORD']:
        pass_input.send_keys(char)
        time.sleep(0.08)

    driver.execute_script("""
        var input = arguments[0];
        var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        nativeSetter.call(input, arguments[1]);
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
    """, pass_input, os.environ['CAESARS_PASSWORD'])

    buttons = driver.find_elements(By.TAG_NAME, 'button')
    for btn in buttons:
        if btn.text.strip() in ('SIGN IN', 'Sign In') and btn.is_displayed():
            btn.click()
            break
    time.sleep(8)
    print(f"Logged in: {driver.current_url}")

    # Go to offers
    print("\nNavigating to offers...")
    driver.get('https://www.caesars.com/rewards/offers')
    time.sleep(5)

    # Scroll to load all sections
    for _ in range(5):
        driver.execute_script("window.scrollBy(0, 800);")
        time.sleep(1)
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(2)

    # Dump ALL aria-labels
    print("\n=== ARIA LABELS ===")
    elements = driver.find_elements(By.CSS_SELECTOR, '[aria-label]')
    for el in elements:
        label = el.get_attribute('aria-label')
        tag = el.tag_name
        href = el.get_attribute('href') or ''
        onclick = el.get_attribute('onclick') or ''
        role = el.get_attribute('role') or ''
        if 'see more' in label.lower() or 'offer' in label.lower() or 'expir' in label.lower():
            print(f"  tag={tag} role={role} aria-label=\"{label}\" href=\"{href}\" onclick=\"{onclick[:50]}\"")

    # Dump ALL links with 'offers' or 'group' in href
    print("\n=== OFFER LINKS ===")
    links = driver.find_elements(By.TAG_NAME, 'a')
    for link in links:
        href = link.get_attribute('href') or ''
        text = link.text.strip()[:40]
        if 'offers' in href.lower() or 'group' in href.lower():
            print(f"  text=\"{text}\" href=\"{href}\"")

    # Dump the section headers and their nearby elements
    print("\n=== SECTION HEADERS ===")
    headers = driver.execute_script("""
        var results = [];
        var all = document.querySelectorAll('*');
        for (var i = 0; i < all.length; i++) {
            var text = all[i].textContent.trim();
            if (text.match(/^(EXPIRING|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\\s+(OFFERS|IN THE)/i) && text.length < 50) {
                var parent = all[i].parentElement;
                var sibling = all[i].nextElementSibling;
                var siblingHref = '';
                if (sibling) {
                    var aTag = sibling.querySelector('a') || sibling;
                    siblingHref = aTag.href || aTag.getAttribute('href') || '';
                }
                results.push({
                    text: text,
                    tag: all[i].tagName,
                    parentTag: parent ? parent.tagName : '',
                    siblingHref: siblingHref,
                    outerHTML: all[i].outerHTML.substring(0, 200)
                });
            }
        }
        return results;
    """)
    for h in headers:
        print(f"  {h['text']} | tag={h['tag']} | siblingHref={h['siblingHref'][:80]}")

    # Also check for data attributes or onclick handlers on See More
    print("\n=== SEE MORE ELEMENTS DETAIL ===")
    see_more = driver.execute_script("""
        var results = [];
        var all = document.querySelectorAll('[aria-label*="See more"]');
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            // Find the nearest <a> tag
            var link = el.querySelector('a') || el.closest('a');
            var parentLink = el.parentElement ? el.parentElement.querySelector('a') : null;
            results.push({
                ariaLabel: el.getAttribute('aria-label'),
                tag: el.tagName,
                href: link ? link.href : '',
                parentHref: parentLink ? parentLink.href : '',
                innerHTML: el.innerHTML.substring(0, 300),
                outerHTML: el.outerHTML.substring(0, 200),
            });
        }
        return results;
    """)
    for s in see_more:
        print(f"\n  label: {s['ariaLabel']}")
        print(f"  tag: {s['tag']}")
        print(f"  href: {s['href']}")
        print(f"  parentHref: {s['parentHref']}")
        print(f"  outerHTML: {s['outerHTML'][:150]}")

finally:
    driver.quit()
    print("\nDone.")
