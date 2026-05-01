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

    offers = _extract_offers(page)

    print(f"  Tier: {snap['tier_status']} | Points: {snap['points_balance']}")
    print(f"  Found {len(offers)} offers")
    for o in offers:
        desc = (o.get("description") or "—")[:50]
        oid = o.get("offerCode") or "—"
        print(f"    • {o.get('title', '?')[:40]:<40}  desc={desc}  id={oid}")
    return snap, offers


def _extract_offers(page: Page) -> list[dict]:
    """Pull each Rio offer card from the DOM.

    Each card is a <div class="c-dashboard--offer-wrapper"> containing:
      - h5.c-offer__heading                    → title
      - span.c-offer__valid (1-2 of them)      → "Book Offer:", "Stay Dates:", or "Offer Valid:" + date range
      - div.c-offer__description                → description (often duplicates image alt)
      - a.c-offer__button[href]                 → contains offerCode=NNNN, our stable id (when present)
    """
    return page.evaluate("""
        () => Array.from(document.querySelectorAll('.c-dashboard--offer-wrapper')).map(card => {
            const txt = el => el ? (el.innerText || el.textContent || '').trim() : '';
            const title = txt(card.querySelector('.c-offer__heading')) || null;

            // Description: prefer .c-offer__description; fall back to img alt
            // (Rio's image alt almost always mirrors the description text).
            let description = txt(card.querySelector('.c-offer__description')) || null;
            if (!description) {
                const imgAlt = card.querySelector('img[alt]')?.getAttribute('alt');
                if (imgAlt) description = imgAlt.trim() || null;
            }

            const dateLines = Array.from(card.querySelectorAll('.c-offer__valid'))
                .map(s => txt(s)).filter(Boolean);
            const href = card.querySelector('a.c-offer__button')?.getAttribute('href') || null;
            let offerCode = null;
            if (href) {
                const m = href.match(/[?&]offerCode=([^&]+)/);
                if (m) offerCode = m[1];
            }
            return { title, description, dateLines, href, offerCode };
        })
    """) or []


def _save_snapshot(s: dict) -> None:
    res = supabase.table("rio_rewards_snapshots").insert(s).execute()
    if getattr(res, "error", None):
        print(f"  ❌ Snapshot save error: {res.error}")
    else:
        print("  💾 Saved Rio snapshot")


# Titles that are perpetual benefits, not time-bound offers — skip them.
EXCLUDED_TITLES = {
    "Your Rio Rewards Member Discount",
}


def _save_offers(offers: list[dict]) -> None:
    """Manual select-then-update/insert. Avoids requiring specific Postgres
    unique constraints to be installed on the table — works on whatever
    schema you have as long as the columns exist.

    Dedup key is (offer_code or title) + valid_start + valid_end. Same offer
    re-listed for a different date window creates a new row (intentional —
    we keep the history of validity periods)."""
    saved = skipped = 0
    for o in offers:
        title = o.get("title")
        if not title:
            continue
        if title in EXCLUDED_TITLES:
            skipped += 1
            continue

        date_range = _pick_date_range(o.get("dateLines") or [])
        valid_start, valid_end = _split_date_range(date_range)

        row = {
            "title": title,
            "description": o.get("description"),
            "offer_code": o.get("offerCode"),
            "url": o.get("href"),
            "valid_start": valid_start,
            "valid_end": valid_end,
        }

        try:
            existing_id = _find_existing(o.get("offerCode"), title, valid_start, valid_end)
            if existing_id is not None:
                supabase.table("rio_offers").update(row).eq("id", existing_id).execute()
            else:
                supabase.table("rio_offers").insert(row).execute()
            saved += 1
        except Exception as e:
            print(f"  ❌ {title[:40]}: {e}")
    print(f"  💾 Saved {saved} offers" + (f" (skipped {skipped} excluded)" if skipped else ""))


def _find_existing(offer_code: str | None, title: str, valid_start, valid_end):
    """Same offer_code + same date window = same row (update).
    Same offer_code + different dates = different row (insert).
    No offer_code: dedup on title + dates."""
    q = supabase.table("rio_offers").select("id")
    if offer_code:
        res = (q.eq("offer_code", offer_code)
                .eq("valid_start", valid_start)
                .eq("valid_end", valid_end)
                .limit(1).execute())
    else:
        res = (q.eq("title", title)
                .eq("valid_start", valid_start)
                .eq("valid_end", valid_end)
                .limit(1).execute())
    return res.data[0]["id"] if res.data else None


def _pick_date_range(date_lines: list[str]) -> str | None:
    """From ['Book Offer: Now - May 18, 2026', 'Stay Dates: Now - May 18, 2026']
    return 'Now - May 18, 2026'. Prefer Stay Dates → Offer Valid → Book Offer."""
    by_kind: dict[str, str] = {}
    for line in date_lines:
        m = re.match(r"(Book Offer|Stay Dates|Offer Valid)\s*:\s*(.+)", line, re.I)
        if m:
            by_kind[m.group(1).lower()] = m.group(2).strip()
    return (
        by_kind.get("stay dates")
        or by_kind.get("offer valid")
        or by_kind.get("book offer")
    )


def _split_date_range(date_range: str | None) -> tuple[str | None, str | None]:
    if not date_range:
        return None, None
    parts = re.split(r"\s*[-–]\s*", date_range, maxsplit=1)
    if len(parts) != 2:
        return None, parse_date(date_range)
    start = today_iso() if parts[0].strip().lower() == "now" else parse_date(parts[0].strip())
    end = parse_date(parts[1].strip())
    return start, end
