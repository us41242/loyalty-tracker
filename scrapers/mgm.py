"""MGM Rewards scraper. Python port of mgm.js.

MGM uses Akamai bot detection on /identity. Camoufox's Firefox-with-patched-
fingerprint approach handles this far better than puppeteer-extra-stealth did.
The first run will likely require you to complete any CAPTCHA manually in the
visible browser; once cookies seed into firefox-profile/ subsequent runs go
through cleanly.
"""

import os
import random
import re
import time

from playwright.sync_api import BrowserContext, Page

from .browser import (
    debug_snapshot,
    human_navigate,
    human_scroll,
    new_page,
    random_delay,
)
from .db import parse_date, supabase
from .session_cookies import load_cookies, save_cookies


def scrape_mgm(browser: BrowserContext) -> dict | None:
    """Scrape + save MGM. Returns the rewards snapshot dict (or None on hard
    failure) so the combined runner can validate fields and decide on a retry."""
    print("\n═══════════════════════════════════════")
    print("  MGM REWARDS SCRAPER")
    print("═══════════════════════════════════════\n")

    # Pull saved cookies BEFORE opening any page so they're set for the first navigation.
    load_cookies(browser, "mgm")

    page = new_page(browser)
    rewards = None
    try:
        # MGM balances only render after the OAuth/login flow runs (a cold
        # /rewards/ visit returns an empty shell). Run the login flow to refresh
        # the token — it rides the saved session; cred entry is harmless if
        # already authed. Wrapped so an MFA-enroll dead-end doesn't abort.
        try:
            _login(page)
        except Exception as e:
            print(f"  ⚠️ login/refresh note: {e}")
        save_cookies(browser, "mgm")
        rewards = _scrape_rewards(page)
        # HARD non-null check: never save/move on with missing balances. Fail loud
        # so the run goes red (session expired / markup changed) instead of silently
        # storing nulls.
        missing = [k for k in ("tier_status", "tier_credits", "rewards_points") if rewards.get(k) is None]
        if missing:
            debug_snapshot(page, "mgm-rewards-null")
            raise RuntimeError(f"MGM: {missing} null after load — session likely expired or markup changed; refresh mgm cookies")
        trips = _scrape_trips(page)
        _save_snapshot(rewards)
        _save_trips(trips)
        save_cookies(browser, "mgm")  # refresh the rolling session for the next run
        print("\n✅ MGM scrape complete!\n")
    except Exception as e:
        print(f"❌ MGM error: {e}")
        debug_snapshot(page, "mgm-error")
        raise  # propagate so run.py marks MGM failed + exits non-zero (no silent pass)
    finally:
        page.close()
    return rewards


def _login(page: Page) -> None:
    print("🔑 Logging in to MGM...")

    # Warm cookies on the homepage first.
    human_navigate(page, "https://www.mgmresorts.com/")
    random_delay(2000, 4000)
    human_scroll(page, 300)
    random_delay(1000, 2000)

    # Already logged in? Skip the form.
    if _appears_logged_in(page):
        print("  ✓ Existing session detected, skipping login form")
        return

    human_navigate(
        page,
        "https://www.mgmresorts.com/identity/?client_id=mgm_app_web"
        "&redirect_uri=https://www.mgmresorts.com/rewards/&scopes=",
    )
    random_delay(3000, 6000)
    debug_snapshot(page, "mgm-login-page")

    body = page.evaluate("() => document.body.innerText.slice(0, 300)")
    print("  Page text:", body.replace("\n", " ")[:200])

    if any(k in body.lower() for k in ("error", "oops", "blocked")):
        print("  ⚠️ MGM showing error / block screen — bot wall hit")
        debug_snapshot(page, "mgm-blocked")
        # Wait for human to solve any visible challenge, then continue.
        if not page.query_selector("#email"):
            print("  Pausing 30s — solve any challenge in the open window, then we continue")
            time.sleep(30)

    if page.query_selector("#email"):
        page.click("#email")
        random_delay(500, 1000)
        for ch in os.environ["MGM_EMAIL"]:
            page.keyboard.type(ch, delay=random.randint(80, 200))
        random_delay(1000, 2000)
        _click_submit(page)
        print("  Clicked Next")
        random_delay(3000, 5000)
        debug_snapshot(page, "mgm-after-next")

        if page.query_selector('input[type="password"]'):
            page.click('input[type="password"]')
            random_delay(500, 1000)
            for ch in os.environ["MGM_PASSWORD"]:
                page.keyboard.type(ch, delay=random.randint(80, 200))
            random_delay(1000, 2000)
            _click_submit(page)
            print("  Clicked Sign In")
            random_delay(5000, 8000)
        else:
            print("  ⚠️ No password field appeared after Next")
            debug_snapshot(page, "mgm-no-password")
    else:
        print("  ⚠️ No email input found")

    # Wait for the post-login redirect to actually settle before judging
    # whether login succeeded — clicking Sign In returns control before the
    # SPA route changes, so a naive URL check fires falsely.
    try:
        page.wait_for_url(re.compile(r"/rewards"), timeout=15000)
    except Exception:
        pass

    print(f"  URL after login: {page.url}")
    text = page.evaluate("() => document.body.innerText") or ""
    if "/identity" in page.url and "Sign Out" not in text:
        debug_snapshot(page, "mgm-login-failed")


def _click_submit(page: Page) -> None:
    btn = page.query_selector('button[type="submit"]')
    if not btn:
        return
    box = btn.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        random_delay(200, 500)
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def _appears_logged_in(page: Page) -> bool:
    """Poll the rewards page for a logged-in signal — the balances render a few
    seconds after load, so a single early check false-negatives."""
    try:
        page.goto("https://www.mgmresorts.com/rewards/", wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        import time as _t
        deadline = _t.time() + 15
        while _t.time() < deadline:
            try:
                text = page.evaluate("() => document.body.innerText")
            except Exception:
                text = ""
            if "/signin" not in page.url and (
                ("Sign In" not in text and "Sign Out" in text)
                or "Tier Credit" in text or "Rewards Points" in text):
                return True
            random_delay(1500, 2000)
        return False
    except Exception:
        return False


def _scrape_rewards(page: Page) -> dict:
    print("📊 Scraping MGM rewards...")
    human_navigate(page, "https://www.mgmresorts.com/rewards/")
    # Balances hydrate a few seconds after load — poll until the tier/points
    # content appears (a cold first visit otherwise reads an empty shell → all
    # None, which is what happened without a warm-up navigation).
    import time as _t
    text = ""
    deadline = _t.time() + 25
    while _t.time() < deadline:
        try:
            text = page.evaluate("() => document.body.innerText") or ""
        except Exception:
            text = ""
        if re.search(r"Tier Credits|Rewards Points|FREEPLAY", text, re.I):
            break
        random_delay(1500, 2500)

    def grab(pattern, group=1, flags=re.I):
        m = re.search(pattern, text, flags)
        return m.group(group) if m else None

    def grab_int(pattern):
        v = grab(pattern)
        return int(v.replace(",", "")) if v else None

    def grab_float(pattern):
        v = grab(pattern)
        return float(v.replace(",", "")) if v else None

    data = {
        "tier_status": grab(r"(Sapphire|Pearl|Gold|Platinum|Noir)"),
        "tier_credits": grab_int(r"([\d,]+)\s*Tier Credits"),
        "tier_credits_to_next": grab_int(r"([\d,]+)\s*to advance to\s+\w+"),
        "tier_next": grab(r"[\d,]+\s*to advance to\s+(\w+)"),
        "rewards_points": grab_int(r"MGM Rewards Points\s*([\d,]+)")
            or grab_int(r"([\d,]+)\s*\$[\d.]+\s*in comps"),
        "rewards_comps_value": grab_float(r"\$([\d.]+)\s*in comps"),
        "freeplay": grab_float(r"FREEPLAY[®]?\s*\$([\d.]+)"),
        "slot_dollars": grab_float(r"SLOT DOLLARS[®]?\s*\$([\d.]+)"),
        "holiday_gift_points": grab_float(r"Holiday Gift Points\s*([\d,.]+)"),
        "milestone_rewards": grab_int(r"(\d+)\s*Milestone Rewards"),
    }

    print(f"  Tier: {data['tier_status']} | Credits: {data['tier_credits']} | Points: {data['rewards_points']}")
    return data


def _scrape_trips(page: Page) -> list[dict]:
    print("📋 Scraping MGM trips...")
    human_navigate(page, "https://www.mgmresorts.com/trips/")
    random_delay(2000, 3000)

    trips: list[dict] = []
    for tab in ("Upcoming", "Past"):
        try:
            page.evaluate(
                """(t) => {
                    const els = [...document.querySelectorAll('a, button, span')];
                    const el = els.find(e => e.textContent.trim() === t);
                    if (el) el.click();
                }""",
                tab,
            )
            random_delay(2000, 3000)

            text: str = page.evaluate("() => document.body.innerText")
            if "Make some new memories" in text:
                continue
            for line in text.splitlines():
                m = re.search(r"Confirmation[:\s#]+([A-Z0-9]+)", line, re.I)
                if m:
                    trips.append({"confirmationCode": m.group(1), "tab": tab.lower()})
        except Exception:
            pass

    print(f"  Found {len(trips)} trips")
    return trips


def _save_snapshot(data: dict) -> None:
    # run_ts in Pacific with correct offset; overrides the DB default that stamped
    # Pacific wall-clock as "+00:00" (zulu), which made the instant read wrong.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    data = {**data, "run_ts": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()}
    res = supabase.table("mgm_rewards_snapshots").insert(data).execute()
    if getattr(res, "error", None):
        print(f"  ❌ Snapshot save error: {res.error}")
    else:
        print("  💾 Saved MGM snapshot")


def _save_trips(trips: list[dict]) -> None:
    if not trips:
        return
    for t in trips:
        if not t.get("confirmationCode"):
            continue
        row = {
            "confirmation_code": t["confirmationCode"],
            "property": t.get("property"),
            "check_in": parse_date(t["checkIn"]) if t.get("checkIn") else None,
            "check_out": parse_date(t["checkOut"]) if t.get("checkOut") else None,
            "status": t.get("status", "Active"),
            "tab": t["tab"],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        res = supabase.table("mgm_trips").upsert(row, on_conflict="confirmation_code").execute()
        if getattr(res, "error", None):
            print(f"  ❌ Trip {t['confirmationCode']}: {res.error}")
    print(f"  💾 Saved {len(trips)} trips")
