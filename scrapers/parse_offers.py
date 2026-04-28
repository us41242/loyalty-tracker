"""Re-parse Caesars offers from a saved HTML dump — no browser needed.

Two modes:

    # See parsed offers grouped by section, plus migration warnings
    python -m scrapers.parse_offers debug/offers.html

    # Inspect raw HTML of N offer cards — use this to find selectors for
    # offer_id, expiry date, etc. Run this first before tuning parsing.
    python -m scrapers.parse_offers debug/offers.html --inspect 3

    # Emit JSON for piping
    python -m scrapers.parse_offers debug/offers.html --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, Tag


# ── Selectors we'll iterate on ──────────────────────────────────────────────
# These are first-pass guesses. After running --inspect once we'll lock them
# down based on the real DOM.
OFFER_CARD_SELECTORS = [
    "[data-testid*='offer']",
    "[data-test*='offer']",
    "article[class*='offer']",
    "div[class*='OfferCard']",
    "div[class*='offer-card']",
]

SECTION_HEADER_SELECTORS = [
    "h2", "h3",  # most likely
    "[class*='section'] [class*='title']",
]

SECTION_RE = re.compile(
    r"^(EXPIRING.*?|(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+OFFERS?)\s*\(?\d*\)?",
    re.I,
)
DATE_RE = re.compile(
    r"(?:Expires?|Valid)\s+(?:through\s+)?(?:today|tomorrow|"
    r"(\d{1,2}/\d{1,2}(?:/\d{2,4})?)|"
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:,?\s+\d{4})?))",
    re.I,
)


def find_offer_cards(soup: BeautifulSoup) -> list[Tag]:
    for sel in OFFER_CARD_SELECTORS:
        cards = soup.select(sel)
        if cards:
            return cards
    return []


def detect_section_for(card: Tag) -> str:
    """Walk back through previous siblings + ancestors to find the section heading."""
    node = card
    seen: set[int] = set()
    while node:
        if id(node) in seen:
            break
        seen.add(id(node))
        # Look at preceding siblings
        for sib in node.find_all_previous(string=True, limit=200):
            txt = sib.strip()
            if txt and SECTION_RE.match(txt):
                return SECTION_RE.match(txt).group(1).strip()
        node = node.parent
    return "Unknown"


def extract_offer(card: Tag) -> dict:
    text = card.get_text("\n", strip=True)
    lines = [l for l in text.splitlines() if l.strip()]

    # Try a bunch of stable-id candidates
    offer_id = (
        card.get("data-offer-id")
        or card.get("data-id")
        or card.get("id")
        or _href_offer_id(card)
    )

    # Extract dates
    date_match = DATE_RE.search(text)
    expiry = (date_match.group(1) or date_match.group(2)) if date_match else None
    raw_date_line = next(
        (l for l in lines if re.match(r"^(Expires?|Valid)", l, re.I)),
        None,
    )

    # Title is the first prominent line that isn't a date or "See Details"
    title = None
    for l in lines:
        if re.match(r"^(Expires?|Valid|See Details|Details|Book Now|Reserve)", l, re.I):
            continue
        title = l
        break

    # Property
    prop_el = card.select_one("[class*='Property'], [class*='property'], [class*='Location'], [class*='location']")
    property_ = prop_el.get_text(strip=True) if prop_el else None

    # Description = everything after title that isn't a date/CTA
    description_lines = []
    for l in lines[1:] if title else lines:
        if l == title:
            continue
        if re.match(r"^(Expires?|Valid|See Details|Details|Book Now|Reserve|Save Offer)", l, re.I):
            continue
        description_lines.append(l)
    description = " | ".join(description_lines[:3]) if description_lines else None

    return {
        "offer_id": offer_id,
        "title": title,
        "property": property_,
        "expiry": expiry,
        "raw_date": raw_date_line,
        "description": description,
        "section": detect_section_for(card),
    }


def _href_offer_id(card: Tag) -> str | None:
    for a in card.find_all("a", href=True):
        href = a["href"]
        # Common patterns: /offers/{id}, ?offerId={id}
        m = re.search(r"/offers?/([A-Za-z0-9_-]{6,})", href)
        if m:
            return m.group(1)
        m = re.search(r"[?&]offer[_-]?id=([^&]+)", href, re.I)
        if m:
            return m.group(1)
    return None


def parse_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = find_offer_cards(soup)
    return [extract_offer(c) for c in cards]


# ── Inspect mode ────────────────────────────────────────────────────────────
def inspect(html: str, n: int) -> None:
    soup = BeautifulSoup(html, "html.parser")
    print(f"\n── Trying selectors ──")
    for sel in OFFER_CARD_SELECTORS:
        cards = soup.select(sel)
        print(f"  {sel:<45}  →  {len(cards)} match")

    cards = find_offer_cards(soup)
    if not cards:
        print("\n❌ No offer cards found with current selectors.")
        print("   Open debug/offers.html in a browser, find an offer card,")
        print("   and tell me a class or data-* attribute that identifies it.")
        return

    print(f"\n✅ Using selector that matched {len(cards)} cards. Showing first {min(n, len(cards))}:\n")
    for i, card in enumerate(cards[:n]):
        print(f"━━━━━━━━━━━━ Card #{i + 1} ━━━━━━━━━━━━")
        # Print attributes
        print(f"  tag: <{card.name} {' '.join(f'{k}={v!r}' for k, v in card.attrs.items())[:200]}>")
        print(f"  text:\n    " + card.get_text("\n    ", strip=True)[:400])
        # Print first <a href> if any
        a = card.find("a", href=True)
        if a:
            print(f"  first link: {a['href']}")
        print()


# ── Audit ───────────────────────────────────────────────────────────────────
def audit(offers: list[dict]) -> None:
    by_section: dict[str, list[dict]] = defaultdict(list)
    for o in offers:
        by_section[o.get("section") or "Unknown"].append(o)

    print(f"\nParsed {len(offers)} offers across {len(by_section)} sections:\n")
    for section, items in by_section.items():
        print(f"━━ {section}  ({len(items)})")
        for o in items:
            t = (o.get("title") or "(no title)")[:50]
            e = (o.get("expiry") or "—")
            oid = (o.get("offer_id") or "—")[:18]
            print(f"   • {t:<50}  exp={e:<12} id={oid}")
        print()

    # Missing field counts
    missing_title = sum(1 for o in offers if not o.get("title"))
    missing_expiry = sum(1 for o in offers if not o.get("expiry"))
    missing_id = sum(1 for o in offers if not o.get("offer_id"))
    print(f"⚠️  missing title: {missing_title}  |  missing expiry: {missing_expiry}  |  missing offer_id: {missing_id}")

    # Cross-section
    title_sections: dict[str, set[str]] = defaultdict(set)
    for o in offers:
        if o.get("title"):
            title_sections[o["title"]].add(o.get("section") or "Unknown")
    cross = {t: s for t, s in title_sections.items() if len(s) > 1}
    if cross:
        print(f"\n🔀 {len(cross)} title(s) appear in multiple sections (the migration case):")
        for t, sects in cross.items():
            print(f"   {t[:60]}  →  {' / '.join(sorted(sects))}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--inspect", type=int, default=0,
                    help="Dump raw HTML+attrs of first N offer cards (0=skip)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    html = args.path.read_text(encoding="utf-8", errors="ignore")

    if args.inspect:
        inspect(html, args.inspect)
        return 0

    offers = parse_html(html)
    if args.json:
        print(json.dumps(offers, indent=2, ensure_ascii=False))
    else:
        audit(offers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
