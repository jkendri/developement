"""N0NBH HamQSL solar-terrestrial banner (hourly)."""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from typing import Any

BANNER_URL = "https://www.hamqsl.com/solar101vhf.php"
PAGE_URL = "https://www.hamqsl.com/solar.html"
CACHE_SEC = 3600.0
USER_AGENT = "ftdx1200-control/1.0 (local ham CAT; N0NBH banner)"

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "body": None, "ctype": "image/gif"}


def fetch_solar_banner(*, force: bool = False) -> tuple[bytes, str]:
    now = time.monotonic()
    with _lock:
        body = _cache["body"]
        if not force and body and now - float(_cache["at"] or 0) < CACHE_SEC:
            return body, str(_cache["ctype"] or "image/gif")
    req = urllib.request.Request(
        BANNER_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/gif,image/*,*/*",
            "Referer": PAGE_URL,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            ctype = str(resp.headers.get("Content-Type") or "image/gif").split(";")[0].strip()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HamQSL solar banner HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HamQSL solar banner failed: {exc.reason}") from exc
    if not data:
        raise RuntimeError("HamQSL solar banner was empty.")
    if not ctype.startswith("image/"):
        ctype = "image/gif"
    with _lock:
        _cache["at"] = time.monotonic()
        _cache["body"] = data
        _cache["ctype"] = ctype
    return data, ctype
