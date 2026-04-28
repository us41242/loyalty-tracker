"""Supabase client + date parsing — Python port of db.js."""

import os
import re
from datetime import date

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"],
)

_SHORT_MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
_FULL_MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1)}


def parse_date(s: str | None) -> str | None:
    """Return ISO YYYY-MM-DD, or the original string if unrecognized, or None."""
    if not s:
        return None
    s = s.strip()

    # MM/DD/YYYY
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}"

    # "Mon DD, YYYY"
    m = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2}),?\s*(\d{4})", s)
    if m and m[1] in _SHORT_MONTHS:
        return f"{m[3]}-{_SHORT_MONTHS[m[1]]}-{int(m[2]):02d}"

    # "Month DD, YYYY"
    m = re.search(r"([A-Z][a-z]+)\s+(\d{1,2}),?\s*(\d{4})", s)
    if m and m[1] in _FULL_MONTHS:
        return f"{m[3]}-{_FULL_MONTHS[m[1]]}-{int(m[2]):02d}"

    return s


def today_iso() -> str:
    return date.today().isoformat()
