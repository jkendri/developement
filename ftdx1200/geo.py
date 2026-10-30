"""Maidenhead locator helpers."""

from __future__ import annotations

from typing import Any


def _letter_index(ch: str, count: int) -> int | None:
    idx = ord(ch) - ord("A")
    if 0 <= idx < count:
        return idx
    return None


def maidenhead_to_latlon(grid: str) -> tuple[float, float] | None:
    """Return the center latitude/longitude of a Maidenhead square."""
    text = "".join(ch for ch in str(grid or "").upper() if ch.isalnum())
    if len(text) < 4:
        return None
    field_lon = _letter_index(text[0], 18)
    field_lat = _letter_index(text[1], 18)
    if field_lon is None or field_lat is None or not text[2].isdigit() or not text[3].isdigit():
        return None
    lon = field_lon * 20.0 - 180.0
    lat = field_lat * 10.0 - 90.0
    lon += int(text[2]) * 2.0
    lat += int(text[3]) * 1.0
    width, height = 2.0, 1.0
    if len(text) >= 6:
        sub_lon = _letter_index(text[4], 24)
        sub_lat = _letter_index(text[5], 24)
        if sub_lon is None or sub_lat is None:
            return None
        lon += sub_lon * (2.0 / 24.0)
        lat += sub_lat * (1.0 / 24.0)
        width, height = 2.0 / 24.0, 1.0 / 24.0
    if len(text) >= 8:
        if not text[6].isdigit() or not text[7].isdigit():
            return None
        lon += int(text[6]) * (2.0 / 240.0)
        lat += int(text[7]) * (1.0 / 240.0)
        width, height = 2.0 / 240.0, 1.0 / 240.0
    return (round(lat + height / 2.0, 6), round(lon + width / 2.0, 6))


def parse_coord(value: Any, *, lo: float, hi: float) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    try:
        num = float(text)
    except (TypeError, ValueError):
        return None
    if num < lo or num > hi:
        return None
    return num
