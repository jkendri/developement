"""Yaesu FTDX-1200 CAT helpers (semicolon-terminated ASCII)."""

from __future__ import annotations

MODES = {
    "1": "LSB",
    "2": "USB",
    "3": "CW",
    "4": "FM",
    "5": "AM",
    "6": "RTTY-L",
    "7": "CW-R",
    "8": "DATA-L",
    "9": "RTTY-U",
    "A": "—",
    "B": "FM-N",
    "C": "DATA-U",
}

MODE_CODES = {name: code for code, name in MODES.items() if name != "—"}

BANDS = [
    {"id": "160", "code": "00", "hz": 1_800_000},
    {"id": "80", "code": "01", "hz": 3_500_000},
    {"id": "40", "code": "03", "hz": 7_000_000},
    {"id": "30", "code": "04", "hz": 10_100_000},
    {"id": "20", "code": "05", "hz": 14_000_000},
    {"id": "17", "code": "06", "hz": 18_068_000},
    {"id": "15", "code": "07", "hz": 21_000_000},
    {"id": "12", "code": "08", "hz": 24_890_000},
    {"id": "10", "code": "09", "hz": 28_000_000},
    {"id": "6", "code": "10", "hz": 50_000_000},
    {"id": "GEN", "code": "11", "hz": 15_000_000},
]


def encode_hz(hz: int) -> str:
    hz = max(30_000, min(56_000_000, int(hz)))
    return f"{hz:08d}"


def format_mhz(hz: int) -> str:
    return f"{hz / 1_000_000:.6f}"


def parse_mhz(text: str) -> int:
    raw = str(text).strip().lower().replace(",", "")
    if not raw:
        raise ValueError("Enter a frequency.")
    if raw.endswith("mhz"):
        raw = raw[:-3].strip()
    if raw.endswith("khz"):
        return int(round(float(raw[:-3].strip()) * 1_000))
    if raw.endswith("hz"):
        return int(round(float(raw[:-2].strip())))
    value = float(raw)
    if value >= 500:
        return int(round(value))
    return int(round(value * 1_000_000))


def split_replies(buf: bytes) -> tuple[list[str], bytes]:
    text = buf.decode("ascii", errors="ignore")
    parts = text.split(";")
    complete = [p.strip() for p in parts[:-1] if p.strip()]
    leftover = parts[-1].encode("ascii", errors="ignore")
    return complete, leftover


def parse_fa(reply: str) -> int | None:
    if len(reply) < 4 or not reply.upper().startswith("FA"):
        return None
    digits = "".join(ch for ch in reply[2:] if ch.isdigit())
    if len(digits) < 8:
        return None
    hz = int(digits[-9:] if len(digits) >= 9 else digits)
    if hz < 30_000 or hz > 56_000_000:
        return None
    return hz


def parse_if(reply: str) -> dict[str, object] | None:
    if len(reply) < 20 or not reply.upper().startswith("IF"):
        return None
    body = reply[2:]
    if len(body) < 24:
        return None
    freq_digits = "".join(ch for ch in body[3:11] if ch.isdigit())
    if len(freq_digits) != 8:
        return None
    freq = int(freq_digits)
    if freq < 30_000 or freq > 56_000_000:
        return None
    mode = body[18:19].upper()
    return {
        "memory": body[0:3],
        "hz": freq,
        "clar": body[11:16],
        "rx_clar": body[16:17] == "1",
        "tx_clar": body[17:18] == "1",
        "mode": MODES.get(mode, mode),
        "mode_code": mode,
        "vfo": "MEM" if body[19:20] == "1" else "VFO-A",
        "split": body[23:24] if len(body) > 23 else "0",
    }


def parse_md(reply: str) -> str | None:
    if len(reply) < 4 or not reply.upper().startswith("MD"):
        return None
    code = reply[3:4].upper()
    name = MODES.get(code)
    if not name or name == "—":
        return None
    return name


def parse_sm(reply: str) -> int | None:
    if len(reply) < 5 or not reply.upper().startswith("SM"):
        return None
    digits = "".join(ch for ch in reply[3:] if ch.isdigit())
    if not digits:
        return None
    return max(0, min(255, int(digits)))


def parse_rm(reply: str) -> tuple[str, int] | None:
    if len(reply) < 6 or not reply.upper().startswith("RM"):
        return None
    kind = reply[2]
    digits = "".join(ch for ch in reply[3:] if ch.isdigit())
    if not digits:
        return None
    return kind, max(0, min(255, int(digits)))


def parse_pc(reply: str) -> int | None:
    if len(reply) < 5 or not reply.upper().startswith("PC"):
        return None
    digits = "".join(ch for ch in reply[2:] if ch.isdigit())
    if not digits:
        return None
    return max(5, min(100, int(digits)))


def parse_ag(reply: str) -> int | None:
    if len(reply) < 5 or not reply.upper().startswith("AG"):
        return None
    digits = "".join(ch for ch in reply[3:] if ch.isdigit())
    if not digits:
        return None
    return max(0, min(255, int(digits)))


def parse_rg(reply: str) -> int | None:
    if len(reply) < 5 or not reply.upper().startswith("RG"):
        return None
    digits = "".join(ch for ch in reply[3:] if ch.isdigit())
    if not digits:
        return None
    return max(0, min(255, int(digits)))


def parse_ps(reply: str) -> bool | None:
    if len(reply) < 3 or not reply.upper().startswith("PS"):
        return None
    return reply[2] != "0"


def parse_tx(reply: str) -> bool:
    return reply.upper().startswith("TX") and len(reply) >= 3 and reply[2] in {"1", "2"}


def parse_id(reply: str) -> str:
    if not reply.upper().startswith("ID"):
        return ""
    return reply[2:]
