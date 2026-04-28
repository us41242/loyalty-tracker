"""Rio Las Vegas rewards scraper. Python port of rio.js."""

import os
import re

from playwright.sync_api import BrowserContext, Page

from .browser import (
    debug_snapshot,
    human_click,
    human_navigate,
    human_type,
    new_page,
    random_delay,
)
from .db import parse_date, supabase, today_iso


def scrape_rio(browser: BrowserContext) -> None:
    print("\n═══════════════════════════════════════")
    print("  RIO REWARDS SCRAPER")
    print("═══════════════════════════════════════\n")

    page = new_page(browser)
    try:
        _login(page)
        snap, offers = _scrape_rewards_and_offers(page)
        _save_snapshot(snap)
        _save_offers(offers)
        print("\n✅ Rio scrape complete!\n")
    except Exception as e:
        print(f"❌ Rio error: {e}")
        debug_snapshot(page, "rio-error")
    finally:
        page.close()


def _login(page: Page) -> None:
    print("🔑 Logging in to Rio...")
    human_navigate(
        page,
        "https://www.riolasvegas.com/api/auth/login?returnTo=/rio-rewards/offers",
    )
    random_delay(2000, 4000)

    email_sel = 'input[name="email"], input[type="email"], input[name="username"]'
    if page.query_selector(email_sel):
        human_type(page, email_sel, os.environ["RIO_USERNAME"])
        random_delay(500, 1000)
        human_type(page, 'input[type="password"]', os.environ["RIO_PASSWORD"])
        random_delay(800, 1500)
        human_click(page, 'button[type="submit"]')
        random_delay(4000, 7000)
    else:
        print("  May already be logged in or different flow")

    print(f"  URL after login: {page.url}")


def _scrape_rewards_and_offers(page: Page) -> tuple[dict, list[dict]]:
    if "/rio-rewards/offers" not in page.url:
        human_navigate(page, "https://www.riolasvegas.com/rio-rewards/offers")
    random_delay(2000, 4000)

    print("📊 Scraping Rio rewards and offers...")
    text: str = page.evaluate("() => document.body.innerText")

    def grab(pattern: str, group: int = 1, flags: int = re.I) -> str | None:
        m = re.search(pattern, text, flags)
        return m.group(group) if m else None

    def grab_int(pattern: str) -> int | None:
        v = grab(pattern)
        return int(v.replace(",", "")) if v else None

    snap = {
        "tier_status": grab(r"(ROUGE|AZUL|GOLD|PLATINUM)\s+MEMBER\s*\|\s*#\d+"),
        "member_number": grab(r"(?:ROUGE|AZUL|GOLD|PLATINUM)\s+MEMBER\s*\|\s*#(\d+)"),
        "points_balance": grab_int(r"([\d,]+)\s*RIO REWARDS POINTS"),
        "resort_credit": float(grab(r"\$([\d,.]+)\s*RESORT CREDIT").replace(",", ""))
            if grab(r"\$([\d,.]+)\s*RESORT CREDIT") else None,
        "points_earned_year": grab_int(r"([\d,]+)\s*POINTS EARNED IN \d{4}"),
        "points_to_next_tier": grab_int(r"([\d,]+)\s*POINTS TO \w+"),
        "next_tier": grab(r"[\d,]+\s*POINTS TO (\w+)"),
        "status_valid_through": parse_date(
            (grab(r"earned (?:.*?) status through\s+([\w\s]+\d{4})") or "").strip() or None
        ),
    }

    offers: list[dict] = []
    after = re.split(r"Your Offers", text, flags=re.I)
    if len(after) > 1:
        lines = [l.strip() for l in after[1].splitlines() if l.strip()]
        for i, line in enumerate(lines):
            m = re.match(r"(?:Offer Valid|Book Offer|Stay Dates):\s*(.+)", line, re.I)
            if not m:
                continue
            title = ""
            for j in range(i - 1, max(-1, i - 6), -1):
                if len(lines[j]) > 5 and not re.match(r"^(Book|Stay|Offer|Valid)", lines[j], re.I):
                    title = lines[j]
                    break
            if title:
                offers.append({"title": title, "dates": m.group(1)})

    print(f"  Tier: {snap['tier_status']} | Points: {snap['points_balance']}")
    print(f"  Found {len(offers)} offers")
    return snap, offers


def _save_snapshot(s: dict) -> None:
    res = supabase.table("rio_rewards_snapshots").insert(s).execute()
    if getattr(res, "error", None):
        print(f"  ❌ Snapshot save error: {res.error}")
    else:
        print("  💾 Saved Rio snapshot")


def _save_offers(offers: list[dict]) -> None:
    saved = 0
    for o in offers:
        if not o.get("title"):
            continue
        valid_start = valid_end = None
        if o.get("dates"):
            parts = re.split(r"\s*[-–]\s*", o["dates"])
            if len(parts) == 2:
                valid_start = today_iso() if parts[0].strip() == "Now" else parse_date(parts[0].strip())
                valid_end = parse_date(parts[1].strip())
        row = {
            "title": o["title"],
            "description": o.get("description"),
            "valid_start": valid_start,
            "valid_end": valid_end,
            "last_seen": today_iso(),
        }
        res = supabase.table("rio_offers").upsert(
            row, on_conflict="title,valid_start,valid_end"
        ).execute()
        if not getattr(res, "error", None):
            saved += 1
    print(f"  💾 Saved {saved} offers")
