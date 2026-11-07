"""Formatting helpers."""

import re
from pathlib import Path


def fmt_size(n):
    if not n: return "?"
    n = float(n)
    for u in ("B", "KB", "MB", "GB"):          # decimal units, as the labels say
        if n < 1000: return f"{n:.1f} {u}"
        n /= 1000
    return f"{n:.1f} TB"


def fmt_duration(sec):
    if not sec: return "?"
    sec = int(sec); h, r = divmod(sec, 3600); m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_date(s):
    s = str(s or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s or "?"


def fmt_views(n):
    return f"{n:,}" if n else "?"


def codec_friendly(v):
    if not v or v == "none": return ""
    v = v.lower()
    if v.startswith("avc") or v.startswith("h264"): return "h264"
    if v.startswith("vp09") or v.startswith("vp9"): return "vp9"
    if v.startswith("av01") or v.startswith("av1"): return "av1"
    if v.startswith("vp8"): return "vp8"
    return v.split(".")[0][:6]


def pretty_dir(path):
    s = str(path); home = str(Path.home())
    return "~" + s[len(home):] if s.startswith(home) else s


def center_in(text, width):
    n = len(text)
    if n >= width: return text[:width]
    pad_l = (width - n) // 2; pad_r = width - n - pad_l
    return " " * pad_l + text + " " * pad_r


_RATE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([KMG]?)(?:I?B)?(?:/?S|PS)?")


def parse_rate(s):
    """'2M' / '500K' / '1.5MB/s' -> bytes per second, or None if it is not a valid rate.
    A bare number counts as bytes only if it is >= 1024 (so '100' is rejected, not 100 B/s)."""
    m = _RATE_RE.fullmatch(str(s).strip().upper())
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2)
    if not unit and 0 < val < 1024:
        return None
    return int(val * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[unit])


def _parse_time(s):
    parts = s.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(p.strip().isdigit() for p in parts):
        raise ValueError("times look like 90, 1:30 or 1:02:03")
    nums = [int(p) for p in parts]
    if len(nums) > 1 and any(n > 59 for n in nums[1:]):
        raise ValueError("minutes and seconds must be below 60")
    t = 0
    for n in nums:
        t = t * 60 + n
    return t


def parse_clip_range(raw):
    raw = raw.strip()
    if not raw or raw.lower() == "whole": return None
    if "-" not in raw: raise ValueError("need MM:SS-MM:SS")
    a, b = raw.split("-", 1)
    start, end = _parse_time(a), _parse_time(b)
    if end <= start:
        raise ValueError("end must be after start")
    return (start, end)


def fmt_views_short(n):
    if not n: return "?"
    n = float(n)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suf
    return str(int(n))


def parse_length_filter(raw):
    """'10-60' | '>20' | '<5' (minutes) -> (min_sec, max_sec); '' clears."""
    raw = (raw or "").strip().replace(" ", "")
    if not raw or raw.lower() in ("none", "clear", "off"):
        return (None, None)
    usage = "use 10-60, >20 or <5 (minutes)"
    try:
        if raw.startswith(">"): return (float(raw[1:]) * 60, None)
        if raw.startswith("<"): return (None, float(raw[1:]) * 60)
        if "-" in raw:
            a, b = raw.split("-", 1)
            lo, hi = float(a) * 60, float(b) * 60
            if hi < lo: raise ValueError("max is below min")
            return (lo, hi)
    except ValueError as e:
        raise ValueError(str(e) if "max is below" in str(e) else usage) from None
    raise ValueError(usage)


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def short_error(exc, limit=200):
    """One readable line from an exception: never empty, never raises."""
    text = _ANSI.sub("", str(exc))
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "") or type(exc).__name__
    if line.startswith("ERROR: "):
        line = line[7:]
    return line[:limit]
