"""Gmail 2FA code fetcher via Google OAuth refresh token. Python port of gmail.js.

With camoufox's persistent profile you should rarely hit this — Caesars only
prompts 2FA when the cookie expires.
"""

import base64
import os
import re
import time
import urllib.parse
import urllib.request


def _access_token() -> str | None:
    cid = os.environ.get("GMAIL_CLIENT_ID")
    secret = os.environ.get("GMAIL_CLIENT_SECRET")
    refresh = os.environ.get("GMAIL_REFRESH_TOKEN")
    if not all([cid, secret, refresh]):
        print("  ⚠️ Gmail OAuth credentials not configured")
        return None

    body = urllib.parse.urlencode({
        "client_id": cid, "client_secret": secret,
        "refresh_token": refresh, "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        import json as _json
        with urllib.request.urlopen(req, timeout=15) as r:
            data = _json.loads(r.read())
        if "error" in data:
            print(f"  ❌ Token refresh failed: {data.get('error_description') or data['error']}")
            return None
        return data.get("access_token")
    except Exception as e:
        print(f"  ❌ Token refresh error: {e}")
        return None


def _gmail_get(url: str, token: str) -> dict:
    import json as _json
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return _json.loads(r.read())


def _extract_code(message: dict) -> str | None:
    snippet = message.get("snippet", "")
    m = re.search(r"\b(\d{6})\b", snippet)
    if m:
        return m.group(1)

    payload = message.get("payload", {})
    body = payload.get("body", {}).get("data")
    if body:
        decoded = base64.urlsafe_b64decode(body + "===").decode("utf-8", "ignore")
        m = re.search(r"\b(\d{6})\b", decoded)
        if m:
            return m.group(1)

    for part in payload.get("parts", []):
        data = part.get("body", {}).get("data")
        if data:
            decoded = base64.urlsafe_b64decode(data + "===").decode("utf-8", "ignore")
            m = re.search(r"\b(\d{6})\b", decoded)
            if m:
                return m.group(1)
    return None


def _manual_entry() -> str | None:
    if os.environ.get("CI"):
        print("  ❌ Cannot prompt for 2FA code in CI environment")
        return None
    return input("  Enter 2FA code from email: ").strip() or None


def fetch_2fa_code() -> str | None:
    token = _access_token()
    if not token:
        return _manual_entry()

    print("  ⏳ Polling Gmail for Caesars MFA code...")
    query = 'from:email@email.caesars-marketing.com subject:"MFA Code" newer_than:5m'
    url = (
        "https://gmail.googleapis.com/gmail/v1/users/me/messages"
        f"?q={urllib.parse.quote(query)}&maxResults=1"
    )

    for attempt in range(1, 13):
        time.sleep(5)
        try:
            res = _gmail_get(url, token)
            messages = res.get("messages", [])
            if messages:
                msg = _gmail_get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{messages[0]['id']}?format=full",
                    token,
                )
                code = _extract_code(msg)
                if code:
                    print(f"  📧 Got 2FA code: {code}")
                    return code
            print(f"  Attempt {attempt}/12 - waiting for email...")
        except Exception as e:
            print(f"  Attempt {attempt}/12 - error: {e}")

    print("  ⚠️ Timed out waiting for email, falling back to manual entry")
    return _manual_entry()
