#!/usr/bin/env python3
"""
Interactive Selenium debug REPL.

Launches Chrome with the same undetected-chromedriver setup as scraper.py,
then drops you into a Python shell with `driver` ready to use.

Usage:
    python debug.py

In the shell:
    driver.get("https://caesars.com")
    driver.find_element(By.CSS_SELECTOR, ".some-class").text
    driver.page_source[:2000]
    # etc.

Type exit() or Ctrl-D to quit. The browser stays open until you close it.
"""

import os
import re
import subprocess
import platform

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from dotenv import load_dotenv
load_dotenv()


def make_driver():
    options = uc.ChromeOptions()
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--start-maximized')
    options.add_argument('--lang=en-US')
    # No --headless — same as scraper.py (Imperva detects it)

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
        match = re.search(r'(\d+)\.', result.stdout)
        chrome_ver = int(match.group(1)) if match else None
    except Exception:
        pass

    print(f"  Chrome version: {chrome_ver or 'auto-detect'}")
    if chrome_ver:
        return uc.Chrome(options=options, use_subprocess=True, version_main=chrome_ver)
    return uc.Chrome(options=options, use_subprocess=True)


def main():
    print("🔧 Loyalty Tracker — Selenium Debug REPL")
    print("   Launching Chrome...\n")

    driver = make_driver()

    banner = """\
Chrome is open. Use `driver` to control it.

Useful imports already loaded:
  By, WebDriverWait, EC, TimeoutException, NoSuchElementException

Quick examples:
  driver.get("https://caesars.com")
  driver.find_element(By.CSS_SELECTOR, "h1").text
  driver.page_source[:2000]
  [el.text for el in driver.find_elements(By.CSS_SELECTOR, ".offer")]

Type exit() or Ctrl-D to quit. The browser stays open until you explicitly close it.
"""

    local_vars = {
        'driver': driver,
        'By': By,
        'WebDriverWait': WebDriverWait,
        'EC': EC,
        'TimeoutException': TimeoutException,
        'NoSuchElementException': NoSuchElementException,
    }

    try:
        import IPython
        IPython.embed(user_ns=local_vars, banner1=banner)
    except ImportError:
        import code
        code.interact(banner=banner, local=local_vars, exitmsg='')


if __name__ == '__main__':
    main()
