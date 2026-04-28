"""Caesars Rewards scraper. Python port of caesars.js.

The persistent firefox profile (firefox-profile/) keeps you signed in between
runs, so 2FA only triggers once. After your first successful login, set
`skip_login=True` (or pass `--skip-login` on run.py) to jump straight to
scraping for fast iteration.

When iterating on offer parsing, run with `dump_html=True` to save
debug/offers.html, then run scrapers/parse_offers.py against that file
without touching the browser at all.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from playwright.sync_api import BrowserContext, Page

from .browser import (
    DEBUG_DIR,
    debug_snapshot,
    human_navigate,
    human_scroll,
    new_page,
    random_delay,
    react_type,
)
from .db import parse_date, supabase
from .gmail_2fa import fetch_2fa_code  # see note below


def scrape_caesars(
    browser: BrowserContext,
    *,
    skip_login: bool = False,
    dump_html: bool = False,
    offers_only: bool = False,
) -> None:
    print("\n═══════════════════════════════════════")
    print("  CAESARS REWARDS SCRAPER")
    print("═══════════════════════════════════════\n")

    page = new_page(browser)
    try:
        if not skip_login:
            _login(page)
            _handle_2fa(page)  # no-op unless the URL is the 2FA step-up page

        if not offers_only:
            rewards = _scrape_rewards_home(page)
            past_res = _scrape_reservations(page, "past")
            current_res = _scrape_reservations(page, "current")

        offers = _scrape_offers(page, dump_html=dump_html)
        # Great Gift balance now renders inline on the offers page — no extra
        # navigation, no 2FA trigger.
        great_gift = _scrape_great_gift_inline(page)

        if not offers_only:
            rewards["great_gift_points"] = great_gift
            _save_snapshot(rewards)
            _save_reservations(past_res + current_res)

        _save_offers(offers)
        print("\n✅ Caesars scrape complete!\n")
    except Exception as e:
        print(f"❌ Caesars error: {e}")
        debug_snapshot(page, "caesars-error")
    finally:
        page.close()


# ── Login ───────────────────────────────────────────────────────────────────
def _login(page: Page) -> None:
    print("🔑 Logging in...")
    human_navigate(page, "https://www.caesars.com/myrewards/profile/signin/")
    random_delay(3000, 5000)

    inputs = page.evaluate("""() => [...document.querySelectorAll('input')].map(i => ({
        type: i.type, name: i.name, id: i.id,
        placeholder: i.placeholder, ariaLabel: i.getAttribute('aria-label'),
        visible: i.offsetWidth > 0 && i.offsetHeight > 0,
    }))""")
    debug_snapshot(page, "caesars-login-page")

    if not inputs:
        random_delay(5000, 8000)
        inputs = page.evaluate("() => [...document.querySelectorAll('input')].map(i => ({type: i.type, visible: i.offsetWidth > 0}))")
        if not inputs:
            text = page.evaluate("() => document.body.innerText.slice(0, 500)")
            print(f"  Page text: {text}")
            raise RuntimeError("Login page didn't render — possible bot detection")

    user_sel = None
    for inp in inputs:
        if not inp.get("visible") or inp.get("type") in ("password", "hidden"):
            continue
        if inp.get("id"):
            user_sel = f"#{inp['id']}"
        elif inp.get("name"):
            user_sel = f'input[name="{inp["name"]}"]'
        elif inp.get("ariaLabel"):
            user_sel = f'input[aria-label="{inp["ariaLabel"]}"]'
        elif inp.get("placeholder"):
            user_sel = f'input[placeholder="{inp["placeholder"]}"]'
        else:
            user_sel = f'input[type="{inp.get("type") or "text"}"]'
        break

    if not user_sel:
        raise RuntimeError("Could not find username input")

    react_type(page, user_sel, os.environ["CAESARS_USERNAME"])
    random_delay(800, 1500)
    react_type(page, 'input[type="password"]', os.environ["CAESARS_PASSWORD"])
    random_delay(1000, 2000)

    clicked = page.evaluate("""() => {
        const btn = [...document.querySelectorAll('button')].find(b =>
            /^(SIGN IN|Sign In|Log In|LOGIN)$/i.test(b.textContent.trim()) && b.offsetWidth > 0);
        if (btn) { btn.click(); return true; }
        return false;
    }""")
    if not clicked:
        page.keyboard.press("Enter")

    random_delay(5000, 8000)
    print(f"  URL after login: {page.url}")
    if "/signin" in page.url:
        debug_snapshot(page, "caesars-login-failed")


def _handle_2fa(page: Page) -> None:
    if "/verification/step-up" not in page.url:
        return

    print("🔐 2FA required...")
    page.wait_for_selector('input[maxlength="1"]', timeout=10000)
    random_delay(8000, 12000)

    code = fetch_2fa_code()
    if not code:
        raise RuntimeError("Could not get 2FA code")

    print(f"  Entering code: {code}")
    boxes = page.query_selector_all('input[maxlength="1"]')
    for i, ch in enumerate(code[: len(boxes)]):
        boxes[i].click()
        random_delay(100, 300)
        page.keyboard.type(ch, delay=__import__("random").randint(80, 180))
        random_delay(200, 500)

    random_delay(2000, 4000)
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    random_delay(2000, 3000)
    print("  ✅ 2FA complete")


# ── Rewards Home ────────────────────────────────────────────────────────────
def _scrape_rewards_home(page: Page) -> dict:
    print("📊 Scraping rewards home...")
    human_navigate(page, "https://www.caesars.com/rewards/home")
    random_delay(2000, 4000)
    text: str = page.evaluate("() => document.body.innerText")

    def grab(pat, group=1, flags=re.I):
        m = re.search(pat, text, flags)
        return m.group(group) if m else None

    def grab_int(pat):
        v = grab(pat)
        return int(v.replace(",", "")) if v else None

    data = {
        "reward_credits": grab_int(r"([\d,]+)\s*REWARD CREDITS"),
        "tier_credits": grab_int(r"([\d,]+)\s*TIER CREDITS"),
        "tier_status": grab(r"(SEVEN STARS|DIAMOND ELITE|DIAMOND PLUS|DIAMOND|PLATINUM|GOLD)"),
        "tier_next": grab(r"[\d,]+\s*to\s+(Seven Stars|Diamond Elite|Diamond Plus|Diamond|Platinum|Gold)"),
        "tier_credits_needed": grab_int(r"([\d,]+)\s*to\s+(?:Seven Stars|Diamond Elite|Diamond Plus|Diamond|Platinum|Gold)"),
        "last_earned_date": parse_date(grab(r"Last credits earned:\s*(\d{2}/\d{2}/\d{4})")),
        "credits_expire_date": parse_date(grab(r"Earn more Reward Credits before\s*(\d{2}/\d{2}/\d{4})")),
    }
    if not data["reward_credits"]:
        debug_snapshot(page, "caesars-rewards-home")
    print(f"  Credits: {data['reward_credits']} | Tier: {data['tier_credits']} {data['tier_status']}")
    return data


# ── Reservations ────────────────────────────────────────────────────────────
def _scrape_reservations(page: Page, tab: str) -> list[dict]:
    print(f"📋 Scraping {tab} reservations...")
    human_navigate(page, "https://www.caesars.com/rewards/stays")

    try:
        page.evaluate(
            """(t) => {
                const el = [...document.querySelectorAll('a, button, span')]
                    .find(l => l.textContent.trim() === t);
                if (el) el.click();
            }""",
            tab.upper(),
        )
        random_delay(2000, 3000)
    except Exception:
        pass

    text: str = page.evaluate("() => document.body.innerText")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    cards: list[dict] = []
    for i, line in enumerate(lines):
        if line != "Property":
            continue
        card = {"tab": tab}
        for j in range(i, min(i + 20, len(lines))):
            label = lines[j]
            value = lines[j + 1] if j + 1 < len(lines) else None
            if label == "Property":
                card["property"] = value
            elif label == "Location":
                card["location"] = value
            elif label == "Check-In":
                card["checkIn"] = value
            elif label == "Checkout":
                card["checkOut"] = value
            elif label == "Adults":
                try: card["adults"] = int(value)
                except: card["adults"] = None
            elif label == "Children":
                try: card["children"] = int(value)
                except: card["children"] = None
            elif label == "Confirmation":
                card["confirmationCode"] = value
        if card.get("confirmationCode"):
            cards.append(card)

    print(f"  Found {len(cards)} {tab} reservations")
    return cards


# ── Offers ──────────────────────────────────────────────────────────────────
def _scrape_offers(page: Page, *, dump_html: bool = False) -> list[dict]:
    print("🎁 Scraping offers...")
    human_navigate(page, "https://www.caesars.com/rewards/offers")
    random_delay(2000, 4000)

    # Clear filters so all sections are visible
    try:
        cleared = page.evaluate("""() => {
            const btn = [...document.querySelectorAll('button, a')]
                .find(e => e.textContent.trim() === 'Clear Filters');
            if (btn) { btn.click(); return true; }
            return false;
        }""")
        if cleared:
            random_delay(2000, 3000)
    except Exception:
        pass

    # Click "See More" until it's gone or we've stalled twice
    stalls = 0
    last_count = -1
    for _ in range(40):
        clicked = page.evaluate("""() => {
            const btns = [...document.querySelectorAll('button, a')]
                .filter(e => /See More/i.test(e.textContent.trim()) && e.offsetWidth > 0);
            for (const b of btns) b.click();
            return btns.length;
        }""")
        if clicked == 0:
            break
        random_delay(1200, 2500)
        human_scroll(page, 600)
        count = page.evaluate("() => document.querySelectorAll('[data-testid*=offer], article, .offer-card').length")
        if count == last_count:
            stalls += 1
            if stalls >= 2:
                break
        else:
            stalls = 0
        last_count = count

    if dump_html:
        out = DEBUG_DIR / "offers.html"
        out.write_text(page.content(), encoding="utf-8")
        print(f"  📄 Dumped offers HTML → {out}")

    text: str = page.evaluate("() => document.body.innerText")
    return parse_offers_text(text)


def parse_offers_text(text: str) -> list[dict]:
    """Extracted so we can re-run it on saved HTML/text without the browser."""
    section_re = re.compile(
        r"^(EXPIRING.*?|(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+OFFERS?)\s*\((\d+)\)",
        re.I,
    )
    expires_re = re.compile(r"^Expires?\s+(today|tomorrow|\d.+)", re.I)
    valid_re = re.compile(r"^Valid\s+(\d.+)", re.I)
    skip_prefix = re.compile(
        r"^(EXPIRING|JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|See More|Clear Filters|FILTER|DESTINATIONS|DATES|OFFER TYPE)",
        re.I,
    )

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    current_section = "Unknown"
    results: list[dict] = []

    for i, line in enumerate(lines):
        ms = section_re.match(line)
        if ms:
            current_section = ms.group(1).strip()
            continue

        m = expires_re.match(line) or valid_re.match(line)
        if not m:
            continue

        # Title = first non-skip line above
        title = ""
        for j in range(i - 1, max(-1, i - 5), -1):
            if skip_prefix.match(lines[j]):
                break
            title = lines[j]
            break

        property_ = ""
        if i >= 2 and not re.match(r"^[A-Z\$\d]", lines[i - 1]) and lines[i - 1] != title:
            property_ = lines[i - 1]

        results.append({
            "title": title or None,
            "section": current_section,
            "property": property_ or None,
            "dates": line,
        })

    return results


# ── Great Gift (inline on offers page, no 2FA) ──────────────────────────────
def _scrape_great_gift_inline(page: Page) -> int | None:
    """The promotions page now renders the balance directly — no click-through.

    Reads the value from <span class="experience-balance-amount-hotfix">.
    """
    try:
        el = page.query_selector("span.experience-balance-amount-hotfix")
        if not el:
            print("  ⚠️ Great Gift balance span not found on page")
            return None
        raw = (el.inner_text() or "").strip().replace(",", "")
        pts = int(raw) if raw.isdigit() else None
        print(f"  🎁 Great Gift Points: {pts}")
        return pts
    except Exception as e:
        print(f"  ⚠️ Could not read Great Gift balance: {e}")
        return None


# ── Save Functions ──────────────────────────────────────────────────────────
def _save_snapshot(data: dict) -> None:
    res = supabase.table("caesars_rewards_snapshots").insert(data).execute()
    if getattr(res, "error", None):
        print(f"  ❌ Snapshot save error: {res.error}")
    else:
        print("  💾 Saved rewards snapshot")


def _save_reservations(reservations: list[dict]) -> None:
    import time as _t
    for r in reservations:
        row = {
            "confirmation_code": r["confirmationCode"],
            "property": r.get("property"),
            "location": r.get("location"),
            "check_in": parse_date(r.get("checkIn")),
            "check_out": parse_date(r.get("checkOut")),
            "adults": r.get("adults"),
            "children": r.get("children"),
            "status": "Active",
            "tab": r["tab"],
            "updated_at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
        }
        res = supabase.table("caesars_reservations").upsert(row, on_conflict="confirmation_code").execute()
        if getattr(res, "error", None):
            print(f"  ❌ Reservation {r['confirmationCode']}: {res.error}")
    print(f"  💾 Saved {len(reservations)} reservations")


def _save_offers(offers: list[dict]) -> None:
    """NOTE: dedup strategy is the open question — see parse_offers.py."""
    import time as _t
    saved = 0
    for o in offers:
        title = o.get("title")
        dates = o.get("dates")
        if not title:
            continue
        offer_id = re.sub(r"\s+", "-", f"{title}-{dates}")[:50]
        row = {
            "offer_id": offer_id,
            "title": title,
            "section": o.get("section"),
            "eligible_properties": o.get("property"),
            "last_seen": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
        }
        res = supabase.table("caesars_offers").upsert(row, on_conflict="offer_id").execute()
        if not getattr(res, "error", None):
            saved += 1
    print(f"  💾 Saved {saved} offers")
