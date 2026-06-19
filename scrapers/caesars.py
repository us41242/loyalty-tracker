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
from .session_cookies import load_cookies, save_cookies


def scrape_caesars(
    browser: BrowserContext,
    *,
    skip_login: bool = False,
    dump_html: bool = False,
) -> None:
    print("\n═══════════════════════════════════════")
    print("  CAESARS REWARDS SCRAPER")
    print("═══════════════════════════════════════\n")

    # Restore the saved Caesars session BEFORE opening a page, so the cookies
    # apply to the first navigation. CI rides this session because Imperva blocks
    # automated cold login. Refresh the session locally (visible login) when it
    # expires — save_cookies() below keeps the rotating Imperva tokens current.
    # In profile mode (--skip-login) the persistent camoufox profile already
    # carries a logged-in session; loading Supabase's IP-bound cookies on top
    # would corrupt it, so skip that.
    had_cookies = False if skip_login else load_cookies(browser, "caesars")

    page = new_page(browser)
    # Bound every implicit wait. Without this, a changed selector makes a click/
    # wait block on Playwright's default until the workflow's timeout-minutes
    # kill the whole job — exactly the May→June "silent hang" (runs sat on the
    # login page for 30 min, then showed up only as a "cancelled" run).
    page.set_default_timeout(20000)
    try:
        if not skip_login:
            _ensure_session(page, had_cookies=had_cookies)

        rewards = _scrape_rewards_home(page)
        past_res = _scrape_reservations(page, "past")
        current_res = _scrape_reservations(page, "current")
        offers = _scrape_offers(page, dump_html=dump_html)
        rewards["great_gift_points"] = _scrape_great_gift(page)

        _save_snapshot(rewards)
        _save_reservations(past_res + current_res)
        _save_offers(offers)

        # Fail loud: zero offers means the scrape silently broke (login wall or
        # changed markup). Caesars always has live offers, so treat 0 as failure
        # rather than letting the run exit green with no data.
        if not offers:
            raise RuntimeError("0 offers scraped — treating as failure (session likely expired or offers markup changed)")

        # Refresh the stored session after a good run so Imperva's rotating
        # tokens (incap_ses_*, reese84, …) stay current for the next CI run.
        save_cookies(browser, "caesars")
        print("\n✅ Caesars scrape complete!\n")
    except Exception as e:
        print(f"❌ Caesars error: {e}")
        debug_snapshot(page, "caesars-error")
        raise  # propagate so run.py exits non-zero and the GitHub run goes red
    finally:
        page.close()


# ── Session ─────────────────────────────────────────────────────────────────
def _looks_logged_in(page: Page) -> bool:
    """Heuristic: the rewards home shows the member's tier/credit balances only
    when authenticated."""
    try:
        body = (page.evaluate("() => document.body.innerText") or "").upper()
    except Exception:
        return False
    return "TIER CREDITS" in body or "REWARD CREDITS" in body


def _ensure_session(page: Page, *, had_cookies: bool) -> None:
    """Prefer the restored cookie session; cold-login only if it isn't valid.

    Cold login is blocked by Imperva in CI, so a failed restore on CI means the
    saved session expired and needs a fresh local (visible) login to refresh the
    cookies in Supabase."""
    if had_cookies:
        try:
            human_navigate(page, "https://www.caesars.com/rewards/home")
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass  # let any Imperva/login redirect settle before we read it
            random_delay(2000, 3500)
            if "/signin" not in page.url and _looks_logged_in(page):
                print("  🔓 Session restored from saved cookies — skipping login")
                return
            print("  ⚠️ Saved session is not valid (expired or IP-bound); falling back to login")
        except Exception as e:
            print(f"  ⚠️ Cookie session check raced a navigation ({e}); falling back to login")

    try:
        page.context.clear_cookies()
        print("  🧹 Cleared stale cookies for a clean cold login")
    except Exception:
        pass
    _login(page)
    _handle_2fa(page)
    if "/signin" in page.url:
        debug_snapshot(page, "caesars-login-failed")
        raise RuntimeError(
            f"No valid saved session and cold login failed (still on {page.url}). "
            "Refresh the session with a local visible login to repopulate Supabase cookies."
        )


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
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass  # let the Imperva JS challenge / redirect settle before we touch the DOM
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

    # Belt-and-suspenders: physically remove any remaining OneTrust overlay so it
    # can't intercept the click on the login fields (headful renders the banner;
    # the userID click was timing out under it).
    try:
        page.evaluate("""() => {
            for (const id of ['onetrust-consent-sdk','onetrust-banner-sdk','ot-sdk-container','onetrust-pc-sdk']) {
                const el = document.getElementById(id); if (el) el.remove();
            }
            const bg = document.querySelector('.onetrust-pc-dark-filter'); if (bg) bg.remove();
            document.body.style.overflow = 'auto';
        }""")
    except Exception:
        pass

    debug_snapshot(page, "caesars-login-page")

    # Target the sign-in fields by their stable names. The old "first visible
    # text input" heuristic now grabs the OneTrust cookie-consent search box /
    # consent checkboxes that share this page, which is what wedged login from
    # May onward (it typed into the wrong element and then hung).
    user_sel = _first_present(page, [
        'input[name="userID"]',
        'input[aria-label="Email, Mobile, Caesars Rewards #"]',
        'input#userID',
    ])
    pass_sel = _first_present(page, [
        'input[name="userPassword"]',
        'input[aria-label="Password"]',
        'input[type="password"]',
    ])
    if not user_sel or not pass_sel:
        text = page.evaluate("() => document.body.innerText.slice(0, 500)")
        print(f"  Page text: {text}")
        raise RuntimeError(f"Login fields not found (user={user_sel}, pass={pass_sel}) — sign-in markup likely changed")

    print(f"  Using fields: {user_sel} / {pass_sel}")
    react_type(page, user_sel, os.environ["CAESARS_USERNAME"])
    random_delay(800, 1500)
    react_type(page, pass_sel, os.environ["CAESARS_PASSWORD"])
    random_delay(1000, 2000)

    # Verify the fields actually hold our values — camoufox/React can drop the
    # synthetic keystrokes. Fall back to Playwright's native fill if empty.
    def _filled():
        try:
            return page.evaluate(
                "(s) => {const u=document.querySelector(s[0]),p=document.querySelector(s[1]);"
                "return {u:(u&&u.value)||'', pl:((p&&p.value)||'').length};}",
                [user_sel, pass_sel])
        except Exception:
            return {"u": "", "pl": 0}
    st = _filled()
    if not st["u"] or not st["pl"]:
        print(f"  ↻ fields empty after react_type (u={st['u']!r} pl={st['pl']}); retrying with page.fill")
        try:
            page.fill(user_sel, os.environ["CAESARS_USERNAME"])
            page.fill(pass_sel, os.environ["CAESARS_PASSWORD"])
            st = _filled()
        except Exception as e:
            print(f"  page.fill failed: {e}")
    print(f"  pre-submit fields: user={st['u']!r} passLen={st['pl']}")

    clicked = page.evaluate("""() => {
        const btn = [...document.querySelectorAll('button')].find(b =>
            /^(SIGN IN|Sign In|Log In|LOGIN)$/i.test(b.textContent.trim()) && b.offsetWidth > 0);
        if (btn) { btn.click(); return true; }
        return false;
    }""")
    if not clicked:
        page.keyboard.press("Enter")

    # Diagnostic: confirm the fields actually held our values and surface any
    # validation error — distinguishes a fill bug from server-side rejection.
    random_delay(3000, 4000)
    try:
        vals = page.evaluate("""() => ({
            user: (document.querySelector('input[name=\"userID\"]')||{}).value || '',
            passLen: ((document.querySelector('input[name=\"userPassword\"]')||{}).value || '').length,
            err: (document.body.innerText.match(/incorrect|invalid|try again|do(?:es)? not match|locked|unable|too many/i)||[''])[0],
            url: location.href
        })""")
        print(f"  post-submit: user={vals.get('user')!r} passLen={vals.get('passLen')} err={vals.get('err')!r}")
    except Exception as e:
        print(f"  post-submit probe failed: {e}")
    debug_snapshot(page, "caesars-postsubmit")

    # Poll for the post-submit outcome instead of checking once too early — the
    # page transitions /signin → (2FA step-up) → /rewards/home, and a single
    # early check caught it mid-flight and bailed.
    import time as _t
    deadline = _t.time() + 35
    while _t.time() < deadline:
        url = page.url
        if "/verification/step-up" in url:
            print("  → 2FA step-up reached")
            return
        if "/signin" not in url:
            print(f"  URL after login: {url}")
            return
        random_delay(1500, 2500)
    print(f"  URL after login (timeout): {page.url}")


def _first_present(page: Page, selectors: list[str], timeout_ms: int = 8000) -> str | None:
    """Return the first selector resolving to a visible element, polling up to
    timeout_ms total. Bounded so a missing field fails fast instead of letting a
    downstream click hang on Playwright's default timeout."""
    import time as _t
    deadline = _t.time() + timeout_ms / 1000
    while _t.time() < deadline:
        for sel in selectors:
            el = page.query_selector(sel)
            if el:
                try:
                    if el.is_visible():
                        return sel
                except Exception:
                    pass
        random_delay(400, 700)
    return None


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

    def read_once() -> dict:
        # Walk every text node (captures CSS-hidden balances). Tier credits show
        # as "62,994 TC", next tier as "12,006 until Diamond Elite".
        text: str = page.evaluate("""() => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const parts = []; let node;
            while ((node = walker.nextNode())) { const t = node.nodeValue.trim(); if (t) parts.push(t); }
            return parts.join(' ');
        }""")
        # Reward Credits is a CSS odometer (digit reels). Each digit COLUMN carries
        # its real value in an aria-label ("<div aria-label='4'>…"), so read those in
        # document order — far more robust than the old geometry guess, which picked
        # the reel's vertical-center digit ("5" out of a 9→0 reel) instead of the
        # bottom-aligned visible one. querySelectorAll preserves left-to-right order.
        reward_credits_odo = page.evaluate(r"""() => {
            const root = document.querySelector('[data-testid="my-rewards-user-detail-dropdown-reward-credits"]');
            if (!root) return null;
            const digits = [...root.querySelectorAll('[aria-label]')]
                .map(el => el.getAttribute('aria-label'))
                .filter(v => /^\d$/.test(v));
            if (!digits.length) return null;
            const n = parseInt(digits.join(''), 10);
            return Number.isFinite(n) ? n : null;
        }""")

        def grab(pat, group=1, flags=re.I):
            m = re.search(pat, text, flags)
            return m.group(group) if m else None

        def grab_int(pat, flags=re.I):
            v = grab(pat, flags=flags)
            return int(v.replace(",", "")) if v else None

        # AUTHORITATIVE reward-credits value: the page's own account summary renders
        # "Reward Credits: 809" (header greeting) and "Reward Credits 809" (tile).
        # The CSS odometer widget is a collapsed dropdown that reads "0" until
        # expanded — actively misleading — so it is NOT used as a value source, only
        # logged for debug. Match the labeled value (colon form first, then the
        # lowercase "809 reward credits" fallback).
        reward_text = (grab_int(r"Reward Credits:\s*([\d,]+)")
                       or grab_int(r"([\d,]+)\s+reward credits\b", flags=0))
        # One-time DOM dump so the odometer reconstruction can be fixed against the
        # real markup if the text match is ALSO wrong.
        try:
            import os as _os
            _os.makedirs("/home/ubuntu/lt/debug", exist_ok=True)
            _html = page.evaluate("""() => { const r = document.querySelector('[data-testid="my-rewards-user-detail-dropdown-reward-credits"]'); return r ? r.outerHTML : 'NO_ROOT'; }""")
            with open("/home/ubuntu/lt/debug/reward_odo.html", "w") as _f:
                _f.write(_html or "EMPTY")
        except Exception:
            pass
        print(f"  [reward-credits debug] text-match={reward_text}  odometer={reward_credits_odo}")

        # Caesars rewards-home (2026-06): "Reward Credits <odometer> · 62,994 TC ·
        # 12,006 until Diamond Elite". 62,994 = EARNED tier credits; 12,006 = to next tier.
        return {
            "reward_credits": reward_text,  # text is authoritative; odometer reads collapsed-dropdown garbage
            "tier_credits": grab_int(r"([\d,]+)\s+TC\b") or grab_int(r"([\d,]+)\s+TIER CREDITS\b"),
            "tier_status": grab(r"(SEVEN STARS|DIAMOND ELITE|DIAMOND PLUS|DIAMOND|PLATINUM|GOLD)"),
            "tier_next": grab(r"until\s+(Seven Stars|Diamond Elite|Diamond Plus|Diamond|Platinum|Gold)") or grab(r"[\d,]+\s+to\s+(Seven Stars|Diamond Elite|Diamond Plus|Diamond|Platinum|Gold)"),
            "tier_credits_needed": grab_int(r"\bTC\s+([\d,]+)\s+until") or grab_int(r"([\d,]+)\s+to\s+(?:Seven Stars|Diamond Elite|Diamond Plus|Diamond|Platinum|Gold)"),
            "last_earned_date": parse_date(grab(r"Last credits earned:\s*(\d{2}/\d{2}/\d{4})")),
            "credits_expire_date": parse_date(grab(r"Earn more Reward Credits before\s*(\d{2}/\d{2}/\d{4})")),
        }

    # WAIT up to 30s for the balances to render AND the reward-credits odometer to
    # SETTLE (two consecutive equal, non-null reads) before trusting it. The page
    # loads async — reading too early gave nulls and a mid-roll odometer digit.
    import time as _t
    data = read_once()
    prev_reward = object()  # sentinel: first compare is always False → forces a re-read
    deadline = _t.time() + 30
    while _t.time() < deadline:
        if (data["tier_credits"] is not None and data["reward_credits"] is not None
                and data["reward_credits"] == prev_reward):
            break
        prev_reward = data["reward_credits"]
        _t.sleep(1.5)
        data = read_once()

    # HARD non-null check: never silently save/return missing balances — fail loud.
    missing = [k for k in ("reward_credits", "tier_credits") if data.get(k) is None]
    if missing:
        debug_snapshot(page, "caesars-rewards-home")
        raise RuntimeError(f"Caesars rewards: {missing} still null after 30s wait — "
                           "page didn't finish loading or the markup changed")

    print(f"  Reward credits: {data['reward_credits']} | Tier credits: {data['tier_credits']} "
          f"({data['tier_credits_needed']} to {data['tier_next']}) | {data['tier_status']}")
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

    # WAIT up to 30s for reservation content to render before reading. The cards
    # load async — reading too early found 0 even though the booking was there
    # (the debug snapshot a beat later had the full Vanderpump reservation).
    # Stop early once a reservation ("Confirmation") OR an empty-state appears.
    import time as _t
    _dl = _t.time() + 30
    while _t.time() < _dl:
        t = page.evaluate("() => document.body.innerText") or ""
        if any(k in t for k in ("Confirmation", "Check-in", "Check-In")) or \
           any(k in t for k in ("No upcoming", "No reservations", "no upcoming", "don't have any")):
            break
        _t.sleep(1.5)

    # The redesigned stays page renders each reservation as FLAT inline text with
    # new labels (no per-line layout, no Location/Adults/Children labels):
    #   "Property The Vanderpump Hotel Check-in Mon, Jun 29 4:00 PM
    #    Check-out Wed, Jul 01 11:00 AM Confirmation RGLWQ Guests 1 Adult, 0 Child"
    # Parse that pattern off the whitespace-collapsed body text.
    from datetime import datetime as _dt
    text: str = page.evaluate("() => document.body.innerText") or ""
    flat = re.sub(r"\s+", " ", text)

    _MON = {m: i for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul",
         "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}

    def _frag_to_iso(frag: str) -> str | None:
        # "Mon, Jun 29 4:00 PM" → "2026-06-29"; roll year forward if the month
        # is already behind us (a Jan booking viewed in Dec is next year).
        m = re.search(r"\b([A-Z][a-z]{2})\s+(\d{1,2})\b", frag or "")
        if not m or m.group(1) not in _MON:
            return None
        mon, day = _MON[m.group(1)], int(m.group(2))
        now = _dt.now()
        year = now.year + (1 if mon < now.month else 0)
        return f"{year}-{mon:02d}-{day:02d}"

    res_re = re.compile(
        r"Property\s+(.+?)\s+Check-in\s+(.+?)\s+Check-out\s+(.+?)\s+"
        r"Confirmation\s+([A-Z0-9]{4,})\s+Guests\s+(\d+)\s+Adults?(?:,\s*(\d+)\s+Child)?",
        re.I,
    )
    cards: list[dict] = []
    for m in res_re.finditer(flat):
        prop, ci, co, conf, adults, children = m.groups()
        cards.append({
            "tab": tab,
            "property": prop.strip(),
            "checkIn": _frag_to_iso(ci),
            "checkOut": _frag_to_iso(co),
            "confirmationCode": conf.strip(),
            "adults": int(adults) if adults else None,
            "children": int(children) if children else None,
        })

    print(f"  Found {len(cards)} {tab} reservations")
    if not cards:
        # No "Property" cards parsed — save the page so we can see if the stays
        # layout changed (the Vanderpump/ex-Cromwell booking should be here).
        debug_snapshot(page, f"caesars-stays-{tab}")
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
    # Stamp run_ts in Pacific with the CORRECT offset (-07:00/-08:00). The DB
    # column default produced Pacific wall-clock mislabeled "+00:00" (zulu), so
    # the instant read wrong; setting it explicitly here overrides that default.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    data = {**data, "run_ts": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()}
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
