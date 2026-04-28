"""Persist browser session cookies to Supabase.

Cross-runner session continuity: log in once on any machine, save cookies to
the shared `session_cookies` table, and any other runner (local laptop or CI)
can resume the session without re-authenticating.

The table is shared with scrape_caesars.py's existing implementation, so the
on-disk shape is Selenium's cookie format (the format that script writes).
This module translates between Selenium-shape and Playwright-shape on the fly.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from playwright.sync_api import BrowserContext

from .db import supabase


# ── Public API ──────────────────────────────────────────────────────────────
def load_cookies(ctx: BrowserContext, site: str) -> bool:
    """Pull saved cookies for `site` and inject into the context.
    Returns True iff cookies were loaded."""
    try:
        res = supabase.table("session_cookies").select("cookies, updated_at").eq("site", site).execute()
    except Exception as e:
        print(f"  🍪 Could not query saved cookies for {site}: {e}")
        return False

    if not res.data:
        print(f"  🍪 No saved cookies for {site}")
        return False

    row = res.data[0]
    raw = json.loads(row["cookies"]) if isinstance(row["cookies"], str) else row["cookies"]
    pw_cookies = [_to_playwright(c) for c in raw if c.get("name") and c.get("value") is not None]
    if not pw_cookies:
        print(f"  🍪 Saved cookie row for {site} was empty")
        return False

    try:
        ctx.add_cookies(pw_cookies)
    except Exception as e:
        print(f"  🍪 add_cookies failed for {site}: {e}")
        return False

    print(f"  🍪 Loaded {len(pw_cookies)} cookies for {site} (saved: {row['updated_at'][:19]})")
    return True


def save_cookies(ctx: BrowserContext, site: str) -> None:
    """Snapshot current context cookies and upsert to Supabase."""
    try:
        pw_cookies = ctx.cookies()
    except Exception as e:
        print(f"  🍪 Could not read cookies: {e}")
        return

    sel_format = [_to_selenium(c) for c in pw_cookies]
    try:
        supabase.table("session_cookies").upsert({
            "site": site,
            "cookies": json.dumps(sel_format),
            "updated_at": datetime.now().isoformat(),
        }, on_conflict="site").execute()
        print(f"  🍪 Saved {len(sel_format)} cookies for {site}")
    except Exception as e:
        print(f"  🍪 Could not save cookies for {site}: {e}")


# ── Cookie shape translation ────────────────────────────────────────────────
_SAMESITE_PW = {"strict": "Strict", "lax": "Lax", "none": "None"}


def _to_playwright(c: dict[str, Any]) -> dict[str, Any]:
    """Selenium → Playwright cookie shape."""
    out: dict[str, Any] = {
        "name": c["name"],
        "value": c["value"],
        "path": c.get("path", "/"),
        "secure": bool(c.get("secure", False)),
        "httpOnly": bool(c.get("httpOnly", False)),
        "sameSite": _SAMESITE_PW.get(str(c.get("sameSite", "lax")).lower(), "Lax"),
    }
    if c.get("domain"):
        out["domain"] = c["domain"]
    # Selenium uses 'expiry' (int seconds), Playwright uses 'expires' (float)
    exp = c.get("expiry") or c.get("expires")
    if exp is not None:
        try:
            out["expires"] = float(exp)
        except (TypeError, ValueError):
            pass
    return out


def _to_selenium(c: dict[str, Any]) -> dict[str, Any]:
    """Playwright → Selenium cookie shape."""
    out: dict[str, Any] = {
        "name": c["name"],
        "value": c["value"],
        "domain": c.get("domain"),
        "path": c.get("path", "/"),
        "secure": bool(c.get("secure", False)),
        "httpOnly": bool(c.get("httpOnly", False)),
        "sameSite": (c.get("sameSite") or "Lax"),
    }
    expires = c.get("expires")
    if expires is not None and expires != -1:
        out["expiry"] = int(expires)
    return out
