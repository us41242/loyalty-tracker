"""Camoufox browser + human-like helpers.

Camoufox is a Firefox build with anti-fingerprinting patched into the binary,
so we don't need stealth plugins. We use a persistent profile so cookies and
sessions survive between runs.
"""

from __future__ import annotations

import os
import random
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from camoufox.sync_api import Camoufox
from playwright.sync_api import BrowserContext, Page

PROFILE_DIR = Path(__file__).resolve().parent.parent / "firefox-profile"
DEBUG_DIR = Path(__file__).resolve().parent.parent / "debug"


def random_delay(min_ms: int = 800, max_ms: int = 2500) -> None:
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


@contextmanager
def launch_browser(headless: bool = False) -> Iterator[BrowserContext]:
    """Yield a camoufox persistent BrowserContext (cookies survive between runs)."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    # CI=true → run headless; otherwise show the window so you can solve any
    # one-off CAPTCHA / 2FA prompts manually.
    if os.environ.get("CI") == "true":
        headless = True

    with Camoufox(
        headless=headless,
        humanize=True,           # mouse moves along Bezier curves
        geoip=True,              # spoofed locale matches the IP's geo
        locale="en-US",
        os=("macos",),
        persistent_context=True,
        user_data_dir=str(PROFILE_DIR),
        window=(1440, 900),
    ) as browser:
        yield browser


def new_page(ctx: BrowserContext) -> Page:
    """Reuse the persistent context's existing tab if it has one, else open a new one."""
    return ctx.pages[0] if ctx.pages else ctx.new_page()


# ── Human-like input ────────────────────────────────────────────────────────
def human_type(page: Page, selector: str, text: str) -> None:
    """Click the field, clear it, type char-by-char with jittered delays."""
    page.wait_for_selector(selector, timeout=15000)
    page.click(selector, click_count=3)  # select existing
    random_delay(200, 400)
    page.keyboard.press("Backspace")
    random_delay(200, 400)
    for ch in text:
        page.keyboard.type(ch, delay=random.randint(60, 180))
    random_delay(200, 500)


def react_type(page: Page, selector: str, text: str) -> None:
    """For React controlled inputs that ignore plain keyboard.type events."""
    human_type(page, selector, text)
    page.evaluate(
        """([sel, val]) => {
            const el = document.querySelector(sel);
            if (!el) return;
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(el, val);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        [selector, text],
    )
    random_delay(300, 600)


def human_click(page: Page, selector: str) -> None:
    """Move to the element with a curve-ish path, then click. Camoufox's
    humanize=True already smooths real mouse moves; this just jitters the
    landing point inside the bounding box."""
    page.wait_for_selector(selector, timeout=15000)
    el = page.query_selector(selector)
    box = el.bounding_box() if el else None
    if box:
        x = box["x"] + box["width"] / 2 + random.uniform(-3, 3)
        y = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
        page.mouse.move(x, y)
        random_delay(100, 300)
        page.mouse.click(x, y)
    else:
        page.click(selector)
    random_delay(500, 1500)


def human_scroll(page: Page, amount: int | None = None) -> None:
    px = amount if amount is not None else random.randint(200, 600)
    page.evaluate("(px) => window.scrollBy({top: px, behavior: 'smooth'})", px)
    random_delay(500, 1500)


def human_navigate(page: Page, url: str, **goto_kwargs) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=30000, **goto_kwargs)
    random_delay(1500, 3500)
    human_scroll(page, random.randint(50, 250))


# ── Debug snapshot ──────────────────────────────────────────────────────────
def debug_snapshot(page: Page, label: str) -> None:
    """Save fullpage screenshot + HTML to debug/ for failure forensics."""
    try:
        png = DEBUG_DIR / f"{label}.png"
        html = DEBUG_DIR / f"{label}.html"
        page.screenshot(path=str(png), full_page=True)
        html.write_text(page.content(), encoding="utf-8")
        print(f"  📸 Debug saved: debug/{label}.png + .html")
    except Exception:
        pass
