"""Entry point: launch one camoufox browser, run the selected scrapers.

Usage:
    python run.py                          # all scrapers, visible window
    python run.py rio                      # just rio
    python run.py mgm caesars              # multiple
    python run.py --headless               # no visible window
"""

import argparse
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from scrapers.browser import launch_browser
from scrapers.caesars import scrape_caesars
from scrapers.mgm import scrape_mgm
from scrapers.rio import scrape_rio

SCRAPERS = {
    "rio": scrape_rio,
    "mgm": scrape_mgm,
    "caesars": scrape_caesars,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("which", nargs="*",
                    help=f"Scrapers to run (default: all). Choices: {', '.join(SCRAPERS)}")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--skip-login", action="store_true",
                    help="Caesars only: skip login + 2FA, assume profile is signed in.")
    ap.add_argument("--dump-html", action="store_true",
                    help="Caesars only: save offers page HTML to debug/offers-*.html.")
    args = ap.parse_args()
    targets = args.which or list(SCRAPERS)
    unknown = [n for n in targets if n not in SCRAPERS]
    if unknown:
        ap.error(f"unknown scraper(s): {unknown}. Choose from: {list(SCRAPERS)}")

    started = time.time()
    pt = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%-m/%-d/%Y, %-I:%M:%S %p")
    print("🚀 Casino Rewards Scraper (Camoufox)")
    print(f"   {pt} PT\n")

    caesars_kwargs = {
        "skip_login": args.skip_login,
        "dump_html": args.dump_html,
    }

    with launch_browser(headless=args.headless) as browser:
        for name in targets:
            try:
                if name == "caesars":
                    scrape_caesars(browser, **caesars_kwargs)
                else:
                    SCRAPERS[name](browser)
            except Exception as e:
                print(f"\n💥 {name} fatal: {e}")

    print(f"\n🏁 Done in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
