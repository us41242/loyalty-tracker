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
from .gmail_2fa import fetch_2fa_code


def scrape_caesars(
    browser: BrowserContext,
    *,
    skip_login: bool = False,
    dump_html: bool = False,
) -> None:
    print("\n═══════════════════════════════════════")
    print("  CAESARS REWARDS SCRAPER")
    print("═══════════════════════════════════════\n")

    page = new_page(browser)
    try:
        if not skip_login:
            _login(page)
            _handle_2fa(page)  # no-op unless the URL is the 2FA step-up page

        rewards = _scrape_rewards_home(page)
        past_res = _scrape_reservations(page, "past")
        current_res = _scrape_reservations(page, "current")
        offers = _scrape_offers(page, dump_html=dump_html)
        rewards["great_gift_points"] = _scrape_great_gift(page)

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
    """Automated login. Credentials come from env (CAESARS_USERNAME /
    CAESARS_PASSWORD), loaded from `.env` locally (via `load_dotenv` in
    scrapers/db.py) or from GitHub Actions secrets in CI.

    Camoufox handles fingerprinting; this function handles the form fill +
    submit. 2FA is finished by `_handle_2fa` if Caesars triggers a step-up.
    """
    print("🔑 Logging in...")
    human_navigate(page, "https://www.caesars.com/myrewards/profile/signin/")
    random_delay(3000, 5000)

    # Dismiss the OneTrust cookie banner if present — it overlays the form
    # and our clicks land on the banner buttons instead of the inputs.
    dismissed = page.evaluate("""() => {
        // Try several known OneTrust selectors in priority order
        const selectors = [
            '#onetrust-accept-btn-handler',
            'button#onetrust-accept-btn-handler',
            '.ot-pc-refuse-all-handler',
            'button[aria-label="Accept All"]',
        ];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.offsetWidth > 0) { el.click(); return sel; }
        }
        // Text-based fallback
        const btn = [...document.querySelectorAll('button')].find(b =>
            /^(Allow All Cookies|Accept All Cookies|Accept All)$/i.test(b.textContent.trim()) && b.offsetWidth > 0);
        if (btn) { btn.click(); return 'text-fallback'; }
        return null;
    }""")
    if dismissed:
        print(f"  🍪 Dismissed cookie banner ({dismissed})")
        random_delay(1500, 2500)

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
    """If Caesars sent us to the 2FA step-up page, fetch the latest code from
    Gmail and enter it. No-op if 2FA wasn't triggered."""
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
    """Pull tier + credits from /rewards/home.

    The page contains a digit-rolling animation that reads as `9 8 7 6 5 4 3 2 1 0
    Reward Credits` — a case-insensitive regex matches the trailing `0` instead
    of the real balance. The actual value renders nearby as lowercase
    `4,663 reward credits`, so we match THAT pattern (case-sensitive lowercase)
    for reward_credits.
    """
    print("📊 Scraping rewards home...")
    human_navigate(page, "https://www.caesars.com/rewards/home")
    random_delay(2000, 4000)

    # The real (non-animated) reward-credits value lives inside the user-detail
    # dropdown (the avatar/profile menu at the top right). It's collapsed by
    # default, which makes its text invisible to innerText. Click it open first.
    page.evaluate("""() => {
        const btn = document.querySelector('[data-testid="my-rewards-dropdown-open-button"]');
        if (btn) btn.click();
    }""")
    random_delay(800, 1500)

    text: str = page.evaluate("() => document.body.innerText")

    def grab(pat, group=1, flags=re.I):
        m = re.search(pat, text, flags)
        return m.group(group) if m else None

    def grab_int(pat, flags=re.I):
        v = grab(pat, flags=flags)
        return int(v.replace(",", "")) if v else None

    data = {
        # Case-sensitive lowercase: skips the digit-rolling animation.
        "reward_credits": grab_int(r"([\d,]+)\s+reward credits\b", flags=0),
        "tier_credits": grab_int(r"([\d,]+)\s+TIER CREDITS\b"),
        "tier_status": grab(r"(SEVEN STARS|DIAMOND ELITE|DIAMOND PLUS|DIAMOND|PLATINUM|GOLD)"),
        "tier_next": grab(r"[\d,]+\s+to\s+(Seven Stars|Diamond Elite|Diamond Plus|Diamond|Platinum|Gold)"),
        "tier_credits_needed": grab_int(r"([\d,]+)\s+to\s+(?:Seven Stars|Diamond Elite|Diamond Plus|Diamond|Platinum|Gold)"),
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


# ── Offers (group URLs + paginated DOM extraction via stable testids) ───────
# Caesars renders the offers list as React Native Web from a preloaded
# state — no API call to chase, no "See More" button, no virtualization.
# Each month has its own URL parameter, and within a month the cards are
# rendered 10/page with a numbered pagination bar.
OFFER_GROUP_URLS = [
    ("current-month", 0),
    ("next-month", 1),
    ("following-next-month", 2),
]


def _month_offset_label(offset: int) -> str:
    """offset=0 → 'MAY OFFERS' for the current month, etc."""
    from datetime import datetime
    today = datetime.now()
    target_year, target_month = today.year, today.month + offset
    while target_month > 12:
        target_month -= 12
        target_year += 1
    return datetime(target_year, target_month, 1).strftime("%B").upper() + " OFFERS"


def _parse_caesars_date_range(s: str | None) -> tuple[str | None, str | None]:
    """'Valid 05.08.26 - 05.09.26' → ('2026-05-08', '2026-05-09'). Single-day
    'Valid 05.08.26' returns the same date for start and end. Also accepts
    'Valid: ...' (colon variant used in the detail-view text)."""
    if not s:
        return (None, None)
    m = re.search(
        r"(?:Valid|Expires?):?\s+(\d{1,2})\.(\d{1,2})\.(\d{2,4})(?:\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4}))?",
        s, re.I,
    )
    if not m:
        return (None, None)

    def _norm(month: str, day: str, year: str) -> str:
        y = int(year)
        if y < 100:
            y += 2000
        return f"{y:04d}-{int(month):02d}-{int(day):02d}"

    start = _norm(m.group(1), m.group(2), m.group(3))
    end = _norm(m.group(4), m.group(5), m.group(6)) if m.group(4) else start
    return (start, end)


def _synth_offer_id(title: str | None, eligible_properties: str | None,
                     valid_start: str | None, valid_end: str | None) -> str:
    """Fallback offer_id when the modal doesn't expose a real Caesars ID.
    Deterministic hash of card content."""
    import hashlib
    raw = "|".join([
        (title or "").strip(),
        (eligible_properties or "").strip(),
        valid_start or "",
        valid_end or "",
    ])
    return "ces-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _parse_offer_details_text(text: str | None) -> tuple[str | None, str | None]:
    """From the offer-details modal's bulk text, return (offer_id, description).

    Observed format:
        {icon} {TITLE}
        Valid: 05.04.26 - 05.10.26
        Offer {OFFER_ID}
        Add to My Calendar
        {DESCRIPTION BODY...}
        AVAILABLE HOTELS & RESORTS  (or AVAILABLE PROPERTIES, etc.)
        {property names}
    """
    if not text:
        return (None, None)
    id_m = re.search(r'\bOffer\s+([A-Z0-9]{6,})\b', text)
    offer_id = id_m.group(1) if id_m else None

    desc_m = re.search(
        r'Add to My Calendar\s+(.+?)(?=\s+AVAILABLE\s+(?:HOTELS|PROPERTIES|RESORTS)\b|\Z)',
        text, re.S | re.I,
    )
    description = desc_m.group(1).strip() if desc_m else None
    if description:
        # Collapse runs of whitespace introduced by inner-text rendering
        description = re.sub(r"\s+", " ", description).strip()
    return offer_id, description


def _read_offer_detail(page: Page) -> dict | None:
    """Read the currently-displayed offer detail. Caesars's offer detail is
    NOT a modal — clicking a card replaces the offer list with a detail view
    inline. We scope reads to the `offer-details` container so we get the
    detail's own title/property/date instead of any sidebar cards.

    Properties: in the LIST view a card has a single `offer-property-list`
    (e.g. 'Las Vegas Resorts'). In the DETAIL view that field isn't always
    present; instead, individual properties are listed under `offer-property`.
    We join them with ', '."""
    return page.evaluate("""() => {
        const idEl     = document.querySelector('[data-testid="offer-display-id"]');
        const detailEl = document.querySelector('[data-testid="offer-details"]');
        if (!idEl || !detailEl) return null;
        const titleEl  = detailEl.querySelector('[data-testid="offer-title"]');
        const dateEl   = detailEl.querySelector('[data-testid="offer-expiration-date-text"]');
        const iconEl   = detailEl.querySelector('img[alt$=" Offer"]');
        // Try the single property-list first (rare in detail view); fall back
        // to joining all individual offer-property nodes.
        const listEl = detailEl.querySelector('[data-testid="offer-property-list"]');
        let properties = listEl ? listEl.innerText.trim() : null;
        if (!properties) {
            const items = [...detailEl.querySelectorAll('[data-testid="offer-property"]')]
                .map(e => e.innerText.trim()).filter(Boolean);
            if (items.length) properties = items.join(', ');
        }
        return {
            display_id_text: idEl.innerText.trim(),
            detail_text: detailEl.innerText,
            title: titleEl ? titleEl.innerText.trim() : null,
            eligible_properties: properties,
            valid_raw: dateEl ? dateEl.innerText.trim() : null,
            category: iconEl ? iconEl.alt.replace(/ Offer$/, '').trim() : null,
        };
    }""")


def _click_next_offer(page: Page) -> bool:
    """Click the in-detail-view 'NEXT offer' arrow. Returns False if the
    button is gone or disabled (i.e. we've reached the last offer)."""
    return page.evaluate("""() => {
        const btn = document.querySelector('[aria-label="View details of NEXT offer."]');
        if (!btn) return false;
        if (btn.getAttribute('aria-disabled') === 'true') return false;
        btn.click();
        return true;
    }""")


def _scrape_one_group(page: Page, group: str, section: str,
                       *, dump_html: bool = False) -> list[dict]:
    url = f"https://www.caesars.com/rewards/offers?group={group}"
    print(f"  📂 {section}: {url}")
    human_navigate(page, url)
    random_delay(2500, 4000)

    # Wait for either an offer card (default list view) or, if Caesars sent us
    # straight into a detail view for some reason, the offer-display-id node.
    try:
        page.wait_for_selector(
            '[data-testid="offer-card"], [data-testid="offer-display-id"]',
            timeout=15000,
        )
    except Exception:
        human_scroll(page, 200)
        try:
            page.wait_for_selector(
                '[data-testid="offer-card"], [data-testid="offer-display-id"]',
                timeout=8000,
            )
        except Exception:
            print(f"     ⚠️ No offers rendered for {section} (URL: {page.url})")
            debug_snapshot(page, f"offers-{group}-no-cards")
            return []

    # Section count from the title (e.g. "MAY OFFERS (67)") for sanity-checking
    expected = page.evaluate("""() => {
        const t = document.querySelector('[data-testid="offer-group-section-title"]');
        if (!t) return 0;
        const m = (t.innerText || '').match(/\\((\\d+)\\)/);
        return m ? parseInt(m[1], 10) : 0;
    }""")
    if expected:
        print(f"     section header reports {expected} offer(s)")

    # Click the first card to drop into detail view; from there we step
    # through each offer with the in-detail "NEXT" arrow.
    page.evaluate("""() => {
        const card = document.querySelector('[data-testid="offer-card"]');
        if (card) card.click();
    }""")

    try:
        page.wait_for_selector('[data-testid="offer-display-id"]', timeout=8000)
    except Exception:
        print(f"     ⚠️ Detail view didn't open for {section}")
        debug_snapshot(page, f"offers-{group}-no-detail")
        return []

    offers: list[dict] = []
    seen: set[str] = set()
    safety_cap = max(expected * 2, 200)  # generous upper bound

    for _ in range(safety_cap):
        random_delay(350, 700)  # let the new offer's detail finish rendering
        data = _read_offer_detail(page)
        if not data:
            break

        # Prefer offer-display-id, fall back to parsing the detail text
        offer_id = None
        if data.get("display_id_text"):
            m = re.search(r"\b([A-Z0-9]{6,})\b", data["display_id_text"])
            if m:
                offer_id = m.group(1)
        if not offer_id:
            fallback_id, _ = _parse_offer_details_text(data.get("detail_text"))
            offer_id = fallback_id

        _, description = _parse_offer_details_text(data.get("detail_text"))

        # Date range — try the testid value, then the detail text as backup
        valid_start, valid_end = _parse_caesars_date_range(data.get("valid_raw"))
        if not valid_start:
            valid_start, valid_end = _parse_caesars_date_range(data.get("detail_text"))

        if not offer_id:
            offer_id = _synth_offer_id(
                data.get("title"),
                data.get("eligible_properties"),
                valid_start,
                valid_end,
            )

        if offer_id in seen:
            # Caesars cycled back to the start — we're done with this section
            break
        seen.add(offer_id)

        offers.append({
            "offer_id": offer_id,
            "title": data.get("title"),
            "description": description,
            "eligible_properties": data.get("eligible_properties"),
            "valid_start": valid_start,
            "valid_end": valid_end,
            "section": section,
            "category": data.get("category"),
        })

        # Step to the next offer; bail when there isn't one
        if not _click_next_offer(page):
            break

    if dump_html:
        out = DEBUG_DIR / f"offers-{group}.html"
        out.write_text(page.content(), encoding="utf-8")

    print(f"     ✓ {len(offers)} offers from {section}")
    return offers


def _scrape_offers(page: Page, *, dump_html: bool = False) -> list[dict]:
    print("🎁 Scraping offers...")
    all_offers: list[dict] = []
    for group, offset in OFFER_GROUP_URLS:
        section = _month_offset_label(offset)
        all_offers.extend(_scrape_one_group(page, group, section, dump_html=dump_html))
    print(f"  📊 Total: {len(all_offers)} offers across {len(OFFER_GROUP_URLS)} months")
    return all_offers


# ── Great Gift Wrap Up balance ──────────────────────────────────────────────
def _scrape_great_gift(page: Page) -> int | None:
    """Read the GGWU balance from the dedicated promotions page.
    Caesars renders the value as <span class="experience-balance-amount-hotfix">."""
    print("🎄 Scraping Great Gift Wrap Up...")
    try:
        human_navigate(page, "https://www.caesars.com/myrewards/promotions/ggwu-points")
        try:
            page.wait_for_selector("span.experience-balance-amount-hotfix", timeout=15000)
        except Exception:
            print("  ⚠️ GGWU balance span not found within 15s")
            debug_snapshot(page, "caesars-great-gift")
            return None
        raw = (page.locator("span.experience-balance-amount-hotfix").first.inner_text() or "").strip().replace(",", "")
        pts = int(raw) if raw.isdigit() else None
        print(f"  🎁 GGWU Points: {pts}")
        return pts
    except Exception as e:
        print(f"  ⚠️ Could not read GGWU balance: {e}")
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
    """Upsert into caesars_offers on offer_id. `timestamp` column is updated
    on every scrape; first-seen tracking would require a schema migration."""
    import time as _t
    if not offers:
        print("  💾 No offers to save")
        return
    now_iso = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
    saved = 0
    errors: list[str] = []
    for o in offers:
        if not o.get("title") or not o.get("offer_id"):
            continue
        row = {
            "offer_id": o["offer_id"],
            "title": o.get("title"),
            "description": o.get("description"),
            "section": o.get("section"),
            "eligible_properties": o.get("eligible_properties"),
            "valid_start": o.get("valid_start"),
            "valid_end": o.get("valid_end"),
            "expires_at": o.get("valid_end"),
            "run_ts": now_iso,
        }
        res = supabase.table("caesars_offers").upsert(row, on_conflict="offer_id").execute()
        err = getattr(res, "error", None)
        if err:
            errors.append(str(err))
        else:
            saved += 1
    suffix = f" ({len(errors)} errors; first: {errors[0][:120]})" if errors else ""
    print(f"  💾 Upserted {saved} offers{suffix}")
