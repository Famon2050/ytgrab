"""YouTube search integration."""

import re
import shutil

from .theme import C, W, paint
from .ui import Spinner, ask_yes_no, prompt, render_table
from .fmt import fmt_duration, fmt_views_short, parse_length_filter


def search_youtube(query, n=10, cfg=None, quiet=False):
    """Return list of result dicts, or [] on failure. quiet=True: no spinner
    or printing (used by the TUI)."""
    try:
        import yt_dlp
    except ImportError:
        return []
    from .ytdl import _apply_auth

    sp = None if quiet else Spinner(f"Searching: {query[:40]}")
    if sp: sp.start()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": n,
    }
    if cfg:
        _apply_auth(opts, cfg)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    except Exception as e:
        if sp: sp.stop()
        if not quiet:
            print(paint(f"  Search failed: {e}", C.RED))
        return []
    if sp: sp.stop()

    entries = info.get("entries") or []
    results = []
    for i, e in enumerate(entries, 1):
        if not e:
            continue
        vid = e.get("id")
        title = e.get("title") or "(untitled)"
        url = e.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
        results.append({
            "index": i,
            "id": vid,
            "title": title,
            "url": url,
            "duration": e.get("duration") or 0,
            "uploader": e.get("uploader") or e.get("channel") or "",
            "views": e.get("view_count") or 0,
        })
    return results


def filter_results(results, lo=None, hi=None):
    """Keep results whose length (seconds) is within [lo, hi]. Results with
    an unknown length are dropped while a filter is active."""
    if lo is None and hi is None:
        return list(results)
    out = []
    for r in results:
        d = r.get("duration") or 0
        if not d:
            continue
        if lo is not None and d < lo: continue
        if hi is not None and d > hi: continue
        out.append(r)
    return out


def parse_selection(raw, n):
    """"1,2,6,7" / "1 3 5-7" / "all"  ->  (numbers, problems).
    Numbers are 1-based, unique, in the order typed (a range counts up, or down
    if written backwards). `problems` lists the bits that could not be used."""
    picked, problems = [], []

    def add(i):
        if i not in picked:
            picked.append(i)

    for tok in [t for t in re.split(r"[,\s;]+", (raw or "").strip().lower()) if t]:
        if tok in ("all", "*"):
            for i in range(1, n + 1):
                add(i)
            continue
        m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", tok)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            step = 1 if lo <= hi else -1
            span = range(lo, hi + step, step)
            bad = [i for i in span if not 1 <= i <= n]
            if bad:
                problems.append(f"{tok} (only 1-{n} are listed)")
            for i in span:
                if 1 <= i <= n:
                    add(i)
            continue
        if tok.isdigit():
            if 1 <= int(tok) <= n:
                add(int(tok))
            else:
                problems.append(f"{tok} (only 1-{n} are listed)")
            continue
        problems.append(f"“{tok}”")
    return picked, problems


def _columns(cols):
    """Widths for the results table so a row never wraps: title grows with the window."""
    show_ch, show_views = cols >= 66, cols >= 86
    used = 2 + 3 + 2 + 2 + 7 + (2 + 14 if show_ch else 0) + (2 + 6 if show_views else 0) + 1
    return max(14, min(64, cols - used)), show_ch, show_views


def _clip_text(text, width):
    text = str(text)
    return text if len(text) <= width else text[:max(1, width - 1)] + "…"


def show_results(results, query, lo=None, hi=None):
    cols = max(40, shutil.get_terminal_size((100, 24)).columns)
    tw, show_ch, show_views = _columns(cols)
    print()
    print(paint("  🔍  Results for: ", C.WHITE, C.BOLD)
          + paint(query, C.AMBER, C.BOLD))
    if lo is not None or hi is not None:
        lo_s = f"{lo / 60:g}" if lo is not None else "0"
        hi_s = f"{hi / 60:g}" if hi is not None else "∞"
        print(paint(f"      length filter: {lo_s}–{hi_s} min", C.GREY))
    print()
    head = f"{'#':>3}  {'Title':<{tw}}  {'Time':>7}"
    if show_ch:
        head += f"  {'Channel':<14}"
    if show_views:
        head += f"  {'Views':>6}"
    print("  " + paint(head, C.GREY_D))
    print("  " + paint("─" * min(W - 4, cols - 3), C.BORDER))
    for n, r in enumerate(results, 1):
        dur = fmt_duration(r["duration"]) if r["duration"] else "?"
        row = (paint(f"{n:>3}", C.RED, C.BOLD) + "  "
               + paint(_clip_text(r["title"], tw).ljust(tw), C.OFF) + "  "
               + paint(dur.rjust(7), C.AMBER, C.BOLD))
        if show_ch:
            row += "  " + paint(_clip_text(r.get("uploader") or "", 14).ljust(14), C.GREY)
        if show_views:
            row += "  " + paint(fmt_views_short(r.get("views")).rjust(6), C.GREY_D)
        print("  " + row)
    print()
    print(paint("  Time is min:sec (or h:mm:ss).", C.GREY_D))
    print()


def _download_selection(cfg, picks):
    """Several results chosen at once: confirm, pick video/audio + quality, download them all."""
    from .flows import batch_download
    from .ytdl import AUDIO_FORMATS, VIDEO_TARGETS

    total = sum(p.get("duration") or 0 for p in picks)
    print()
    print(paint(f"  Selected {len(picks)} videos", C.WHITE, C.BOLD)
          + paint(f"   ·   total time {fmt_duration(total)}" if total else "", C.GREY))
    print()
    for n, p in enumerate(picks, 1):
        dur = fmt_duration(p["duration"]) if p.get("duration") else "?"
        print(paint(f"   {n:>2}  ", C.RED, C.BOLD) + paint(_clip_text(p["title"], 58), C.OFF)
              + paint(f"  {dur}", C.AMBER))
    print()
    if not ask_yes_no(f"Download these {len(picks)} videos?", default=True):
        print(paint("  Cancelled.", C.GREY))
        return False
    print()
    kind_idx = render_table("Download as", [(1, "Video", "MP4", "", False), (2, "Audio only", "", "", False)],
                            default_idx=1 if cfg.get("last_kind", "video") == "video" else 2)
    run = dict(cfg)
    if kind_idx == 1:
        rows = [(i, label, "", "", False) for i, (_, label) in enumerate(VIDEO_TARGETS, 1)]
        cur = next((i for i, (k, _) in enumerate(VIDEO_TARGETS, 1) if k == cfg.get("last_video_quality")), 1)
        idx = render_table("Video quality", rows, default_idx=cur)
        run.update(last_kind="video", last_video_quality=VIDEO_TARGETS[idx - 1][0])
    else:
        rows = [(i, label, detail, "", False) for i, (_, label, detail) in enumerate(AUDIO_FORMATS, 1)]
        cur = next((i for i, (k, _, _) in enumerate(AUDIO_FORMATS, 1) if k == cfg.get("last_audio_format")), 1)
        idx = render_table("Audio format", rows, default_idx=cur)
        run.update(last_kind="audio", last_audio_format=AUDIO_FORMATS[idx - 1][0])
    done, failed = batch_download(run, [p["url"] for p in picks], use_archive=True)
    print(paint(f"  ✓  {done} downloaded", C.GREEN, C.BOLD)
          + (paint(f"   ✕  {failed} failed", C.RED, C.BOLD) if failed else ""))
    prompt("  Press enter to return… ")
    return True


def search_flow(cfg, query):
    """Interactive: search, filter, pick one or several results, download."""
    from .flows import download_one

    results = search_youtube(query, n=20, cfg=cfg)
    if not results:
        print(paint("  No results.", C.GREY))
        prompt("  Press enter to continue… ")
        return False

    lo = hi = None
    while True:
        shown = filter_results(results, lo, hi)
        show_results(shown, query, lo, hi)
        if not shown:
            print(paint("  Nothing matches that length filter.", C.AMBER))
        raw = prompt("  Pick:  2   ·   1,3,6   ·   4-7   ·   all      (f=filter by length, a=refine, Enter=cancel)  › ").strip()
        if not raw:
            return False
        low = raw.lower()
        if low in ("a", "again", "refine"):
            new_q = prompt("  New query  › ")
            if not new_q:
                return False
            return search_flow(cfg, new_q)
        if low in ("f", "filter"):
            spec = prompt("  Length in minutes  (10-60, >20, <5, Enter=clear)  › ")
            try:
                lo, hi = parse_length_filter(spec)
            except ValueError as e:
                print(paint(f"  Invalid filter: {e}", C.RED))
            continue
        nums, problems = parse_selection(raw, len(shown))
        if problems:
            print(paint("  Ignored: " + ", ".join(problems), C.AMBER))
        if not nums:
            print(paint("  Invalid choice.  Type numbers like 2 or 1,3,6 or 4-7 or all.", C.RED))
            continue
        picks = [shown[i - 1] for i in nums]
        if len(picks) == 1:
            pick = picks[0]
            print()
            print(paint("  Selected:  ", C.GREY)
                  + paint(pick["title"], C.WHITE, C.BOLD))
            print()
            download_one(cfg, pick["url"])
            return True
        if _download_selection(cfg, picks):
            return True
