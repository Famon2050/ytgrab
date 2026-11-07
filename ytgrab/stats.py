"""Statistics dashboard for ytgrab. Reads the history file and shows
totals, activity streaks, quality breakdown, and recent downloads."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from .config import load_history
from .fmt import fmt_size
from .theme import C, W, banner, paint


# ----------------------------------------------------------------------
# Card helpers
# ----------------------------------------------------------------------
def _card_top(title):
    head = f"─ {title} "
    n = W - 4 - len(head)
    print(paint("  ╭" + head + "─" * n + "╮", C.BORDER))


def _card_bot():
    print(paint("  ╰" + "─" * (W - 4) + "╯", C.BORDER))


def _card_blank():
    print(paint("  │", C.BORDER) + " " * (W - 4) + paint("│", C.BORDER))


def _kv(label, value):
    label_padded = f"{label:<16}"
    value_str = str(value)
    content = (
        "  " + paint("●", C.RED) + "  "
        + paint(label_padded, C.GREY)
        + paint(value_str, C.WHITE, C.BOLD)
    )
    vis = 2 + 1 + 2 + 16 + len(value_str)
    pad = " " * max(0, 65 - vis)
    print(paint("  │", C.BORDER) + " " + content + pad + paint("│", C.BORDER))


def _section(title):
    line = f"  ── {title} " + "─" * max(0, W - 8 - len(title))
    print(paint(line, C.BORDER))
    print()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except Exception:
        return None


def _gradient_bar(filled, total, width):
    """Right-aligned gradient fill + dim track."""
    from .theme import progress_palette, _rgb
    if total <= 0:
        return paint("░" * width, C.GREY_D)
    filled = max(0, min(width, filled))
    colors = progress_palette(width)
    out = ""
    for i in range(filled):
        out += _rgb(*colors[i]) + "█"
    if filled > 0:
        out += C.R
    out += paint("░" * (width - filled), C.GREY_D)
    return out


# ----------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------
def stats_dashboard(cfg):
    hist = load_history()
    banner(cfg)

    print(paint("  📊  Statistics", C.WHITE, C.BOLD))
    print()

    if not hist:
        print(paint("  No download history yet.", C.GREY))
        print(paint("  Download something first, then come back.", C.GREY_D))
        print()
        return

    # ---- aggregate ----
    total = len(hist)
    total_size = sum((e.get("size") or 0) for e in hist)
    video_n = sum(1 for e in hist if e.get("kind") == "video")
    audio_n = sum(1 for e in hist if e.get("kind") == "audio")

    dates = [d for d in (_parse_date(e.get("date", "")) for e in hist) if d]
    first_dt = min(dates) if dates else None
    last_dt = max(dates) if dates else None

    # bucket by day for charts + streaks
    day_buckets = defaultdict(int)
    for d in dates:
        day_buckets[d.date()] += 1

    today = datetime.now().date()
    streak = 0
    probe = today
    while day_buckets.get(probe, 0) > 0:
        streak += 1
        probe -= timedelta(days=1)

    span_days = ((last_dt.date() - first_dt.date()).days + 1) if (first_dt and last_dt) else 1
    avg_per_day = total / max(span_days, 1)

    # ---- overview card ----
    _card_top("Overview")
    _card_blank()
    _kv("Total downloads",  total)
    _kv("  Video",          video_n)
    _kv("  Audio",          audio_n)
    _kv("Total size",       fmt_size(total_size) if total_size else "—")
    _card_blank()
    if first_dt and last_dt:
        _kv("First",         first_dt.strftime("%Y-%m-%d"))
        _kv("Latest",        last_dt.strftime("%Y-%m-%d"))
        _kv("Days active",   span_days)
        _kv("Avg per day",   f"{avg_per_day:.2f}")
        if streak > 0:
            _kv("Current streak", f"{streak} day{'s' if streak != 1 else ''} 🔥")
    _card_blank()
    _card_bot()
    print()

    # ---- 14-day activity ----
    if dates:
        _section("Last 14 days")
        window = []
        for i in range(13, -1, -1):
            day = today - timedelta(days=i)
            window.append((day.strftime("%m-%d"), day_buckets.get(day, 0)))

        max_v = max(v for _, v in window) or 1
        bar_w = 30
        for label, val in window:
            filled = int(bar_w * val / max_v) if max_v else 0
            bar = _gradient_bar(filled, bar_w, bar_w)
            count = paint(f"{val:>3}", C.AMBER if val else C.GREY_D,
                          C.BOLD if val else "")
            print(f"   {paint(label, C.GREY)}  {bar}  {count}")
        print()

    # ---- quality breakdown ----
    qual_counter = Counter((e.get("quality") or "?").strip() for e in hist)
    if qual_counter:
        _section("By quality")
        top = qual_counter.most_common(6)
        max_q = top[0][1] if top else 1
        for qual, count in top:
            q_label = (qual or "?").ljust(8)
            filled = int(24 * count / max_q) if max_q else 0
            bar = _gradient_bar(filled, 24, 24)
            print(f"   {paint(q_label, C.OFF)}  {bar}  "
                  + paint(str(count), C.AMBER, C.BOLD))
        print()

    # ---- by size ----
    sized = [e for e in hist if e.get("size")]
    if sized:
        largest = max(sized, key=lambda e: e["size"])
        avg_size = total_size / len(sized)
        _section("Size")
        big = largest.get("title", "?")
        if len(big) > 50:
            big = big[:49] + "…"
        print(f"   {paint('Largest', C.GREY):<12}  "
              + paint(fmt_size(largest['size']), C.WHITE, C.BOLD))
        print(f"   {paint('', C.GREY):<12}  " + paint(big, C.OFF))
        print(f"   {paint('Average', C.GREY):<12}  "
              + paint(fmt_size(int(avg_size)), C.WHITE, C.BOLD))
        print()

    # ---- top channels ----
    chan = Counter(e["channel"] for e in hist if e.get("channel"))
    if chan:
        _section("Top channels")
        top = chan.most_common(5)
        max_c = top[0][1]
        for name, count in top:
            label = (name if len(name) <= 24 else name[:23] + "…").ljust(24)
            bar = _gradient_bar(int(20 * count / max_c), 20, 20)
            print(f"   {paint(label, C.OFF)}  {bar}  "
                  + paint(str(count), C.AMBER, C.BOLD))
        print()

    # ---- size per month ----
    month_size = defaultdict(int)
    for e in hist:
        if e.get("size") and len(e.get("date", "")) >= 7:
            month_size[e["date"][:7]] += e["size"]
    if month_size:
        _section("Data per month")
        months = sorted(month_size)[-6:]
        max_m = max(month_size[m] for m in months)
        for m in months:
            bar = _gradient_bar(int(24 * month_size[m] / max_m), 24, 24)
            print(f"   {paint(m, C.GREY)}  {bar}  "
                  + paint(fmt_size(month_size[m]), C.AMBER, C.BOLD))
        print()

    # ---- recent ----
    _section("Recent")
    for e in list(reversed(hist))[:5]:
        t = e.get("title", "?")
        if len(t) > 48:
            t = t[:47] + "…"
        date = e.get("date", "")
        kind = e.get("kind", "?")
        qual = (e.get("quality") or "?")[:6]
        print(paint("   ●  ", C.RED)
              + paint(f"{date}  ", C.GREY_D)
              + paint(f"{kind:<5} ", C.AMBER)
              + paint(f"{qual:<6} ", C.GREY)
              + paint(t, C.OFF))
    print()
