"""ytstream: watch YouTube in VLC without downloading anything.

    ytstream                       the streaming dashboard (search, paste a link, continue, history, channels)
    ytstream https://youtu.be/...  play a link right now
    ytstream lofi hip hop          search, pick, play

ytgrab finds the direct stream with yt-dlp and hands it to VLC. While VLC plays, ytgrab keeps your place
(through VLC's remote-control interface) so the next time you open the video it can continue from the
last minute you watched.
"""

import argparse
import contextlib
import io
import re
import shutil
import sys
import time

from . import home, stream, termui, theme
from .config import load_config, save_config
from .keys import open_keys
from .progress import ProgressRenderer
from .search import parse_selection, search_youtube, show_results
from .theme import C, banner, paint
from .ui import Back, Spinner, ask_yes_no, prompt, render_table

TAGLINE = "stream YouTube in VLC   ·   nothing is downloaded"
OPTIONS = (
    {"id": "search", "label": "Search & play", "desc": "find a video, play it in VLC (1,3,5 queues several)",
     "aliases": ("search",)},
    {"id": "link", "label": "Play a link", "desc": "paste a YouTube video link",
     "aliases": ("link", "url", "play")},
    {"id": "continue", "label": "Continue watching", "desc": "pick up where you left off",
     "aliases": ("continue", "resume")},
    {"id": "history", "label": "History", "desc": "everything you streamed, with your place in it",
     "aliases": ("history",)},
    {"id": "channels", "label": "My channels", "desc": "explore saved creators and watch straight from them",
     "aliases": ("channels", "channel")},
    {"id": "settings", "label": "Settings", "desc": "quality cap, VLC program, fullscreen, resume",
     "aliases": ("settings", "config")},
)
HINTS = (
    "Search YouTube to watch it in VLC…",
    "Paste a YouTube link to play it…",
    "Type 3 to continue where you left off…",
    "Try:  lofi hip hop radio",
    "Use ↑ ↓ to browse, Enter to open…",
)
_URL = re.compile(r"^(https?://)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", re.I)


def looks_like_link(text):
    return bool(_URL.match((text or "").strip()))


def normalise_link(text):
    t = text.strip()
    return t if t.startswith("http") else "https://" + t


# ======================================================================
# the "now playing" panel
# ======================================================================
def render_now_playing(info, sess, cols, now, painter=None, started=None):
    """Five lines shown while VLC plays (pure; `sess` only needs .pos/.length/.rc_ok/.paused)."""
    avail = max(10, cols - 1)
    pad = "  "
    painter = painter or ProgressRenderer()
    pos = sess.pos
    length = 0 if info.get("is_live") else (sess.length or info.get("duration") or 0)     # a live stream has no end
    eq = "▂▃▄▅▆▇▆▅▄▃"
    wave = "".join(eq[(int(now * 9) + i * 3) % len(eq)] for i in range(4)) if not sess.paused else "▂▂▂▂"
    head = pad + paint("▶ " if not sess.paused else "❚❚ ", C.RED_HOT, C.BOLD) \
        + paint("NOW PLAYING IN VLC" if not sess.paused else "PAUSED", C.WHITE, C.BOLD) + "  " + paint(wave, C.RED_HOT)
    sub = " · ".join(x for x in (info.get("channel"), f"{info['height']}p" if info.get("height") else "",
                                 "LIVE" if info.get("is_live") else "") if x)
    lines = [head, pad + paint(termui.clip(info.get("title", ""), avail - 4), C.WHITE, C.BOLD),
             pad + paint(termui.clip(sub, avail - 4), C.GREY)]
    if pos is not None and length:
        frac = max(0.0, min(1.0, pos / length))
        stamp = f"{stream.clock(pos)} / {stream.clock(length)}  {int(frac * 100):>3}%"
        bw = max(8, min(60, avail - len(stamp) - 8))
        bar = "▕" + painter._bar(bw, now, frac, True, "download", bool(theme._COLOR)) + "▏"
        lines.append(pad + bar + " " + paint(stamp, C.AMBER, C.BOLD))
    elif pos is not None:
        lines.append(pad + paint(f"{stream.clock(pos)}", C.AMBER, C.BOLD) + paint("  (live)" if info.get("is_live") else "", C.GREY))
    else:
        wait = "connecting to VLC…" if (sess.rc_ok is False and (started is None or now - started < 8)) \
            else "position tracking is unavailable for this VLC"
        lines.append(pad + paint(termui.spinner(now) + " " + wait, C.AMBER))
    keys_hint = ("space pause   ←→ ±10 s   q stop   or just close VLC" if sess.rc_ok
                 else "q stop   or just close VLC")
    lines.append(pad + paint(termui.clip(keys_hint, avail - 2), C.GREY_D))
    return [termui.clip_ansi(ln, avail) for ln in lines]


# ======================================================================
# playing one video
# ======================================================================
def _vlc_missing():
    print()
    print(paint("  VLC was not found.", C.RED, C.BOLD))
    print(paint("  Install it:  ", C.GREY) + paint(stream.install_hint(), C.WHITE))
    print(paint("  or tell ytstream where it is:  Settings → VLC program", C.GREY))
    print()


def _ask_resume(entry, point):
    dur = int(entry.get("duration") or 0)
    where = f"{stream.clock(point)}" + (f" of {stream.clock(dur)}" if dur else "")
    raw = prompt(f"  Resume from {where}?   Enter = resume   s = start over   c = cancel  › ").strip().lower()
    if raw in ("c", "q", "n"):
        return None
    return 0 if raw in ("s", "start") else point


def play_one(cfg, url, auto_resume=False, resolver=None, session_factory=None, reader="auto", out=None,
             asker=_ask_resume, clock=time.monotonic):
    """Resolve, open VLC, show the panel, remember the position. Returns a result dict or None."""
    vlc = stream.find_vlc(cfg)
    if not vlc:
        _vlc_missing()
        return None
    sp = Spinner("Finding the stream")
    sp.start()
    try:
        info = (resolver or stream.resolve)(url, cfg)
    except stream.StreamError as e:
        sp.stop()
        print(paint(f"\n  Could not get a stream: {e}", C.RED))
        return None
    except Exception as e:                                  # an extractor bug must not end the session
        sp.stop()
        print(paint(f"\n  Could not get a stream: {str(e)[:160]}", C.RED))
        return None
    finally:
        sp.stop()

    entry = stream.get(info.get("id")) or stream.get(url)
    start = 0
    point = stream.resume_point(entry) if (cfg.get("stream_resume", True) and not info.get("is_live")) else 0
    if point:
        start = point if auto_resume else asker(entry, point)
        if start is None:
            print(paint("  Cancelled.", C.GREY))
            return None
    port = stream.free_port()
    cmd = stream.vlc_command(vlc, info, start=start, port=port, fullscreen=cfg.get("stream_fullscreen", False),
                             extra=cfg.get("stream_args", ""))
    stream.record(info, position=start, duration=info.get("duration"), finished=False, new_session=True)
    sess = (session_factory or stream.PlaySession)(cmd, port).start()
    sess.pos = start or None
    user_quit = _watch(sess, info, cfg, reader, out, clock)

    pos = sess.pos if sess.pos is not None else start
    length = sess.length or info.get("duration") or 0
    ended_cleanly = (not user_quit) and sess.returncode == 0
    finished = stream.is_finished(pos or 0, length, ended_cleanly)
    stream.record(info, position=0 if finished else (pos or 0), duration=length, finished=finished)
    crashed = sess.returncode not in (0, None) and not user_quit and (pos or 0) <= start + 1
    if crashed:
        print(paint(f"\n  VLC closed straight away (exit code {sess.returncode}). "
                    "The stream may have expired, or your VLC options are wrong.", C.AMBER))
    elif finished:
        print(paint(f"\n  ✓  Finished: {termui.clip(info['title'], 60)}", C.GREEN, C.BOLD))
    elif pos:
        print(paint(f"\n  Saved your place at {stream.clock(pos)}"
                    + (f" of {stream.clock(length)}" if length else "") + ".  Next time: Continue watching.", C.GREEN))
    return {"position": pos or 0, "length": length, "finished": finished, "quit": user_quit, "title": info["title"]}


def _watch(sess, info, cfg, reader, out, clock):
    """Show the panel until VLC closes (or q). Returns True if the user stopped it from here."""
    out = out or sys.stdout
    tty = bool(getattr(out, "isatty", lambda: False)())
    if reader == "auto":
        reader = open_keys() if tty else None
    painter = ProgressRenderer(style=cfg.get("bar_style", "classic"))
    t0 = clock()
    user_quit = False
    first = True
    try:
        while sess.running:
            if tty:
                lines = render_now_playing(info, sess, termui.size()[0], clock() - t0, painter, 0.0)
                up = "" if first else f"\033[{len(lines)}A"
                out.write(f"{up}\r\033[?25l" + "\n".join(ln + "\033[K" for ln in lines) + "\n")
                out.flush()
                first = False
            tok = reader.read(0.1) if reader else (time.sleep(0.25) or None)
            if tok in ("q", "CTRL_C", "CTRL_D"):
                user_quit = True
                break
            if tok == " ":
                sess.toggle_pause()
            elif tok == "LEFT":
                sess.seek_by(-10)
            elif tok == "RIGHT":
                sess.seek_by(10)
    except KeyboardInterrupt:
        user_quit = True
    finally:
        sess.stop()
        if reader:
            try:
                reader.close()
            except Exception:
                pass
        if tty and not first:
            n = len(render_now_playing(info, sess, termui.size()[0], 0.0, painter, 0.0))
            out.write(f"\033[{n}A\r\033[J\033[?25h")
            out.flush()
    return user_quit


def play_queue(cfg, picks, **kw):
    """Play videos one after another, asking before each next one (Enter, s = skip it, q = stop)."""
    i = 0
    while i < len(picks):
        res = play_one(cfg, picks[i]["url"], **kw)
        if res is None and i == 0:
            return
        i += 1
        if i < len(picks):
            nxt = picks[i]
            ans = prompt(f"  Up next ({i + 1}/{len(picks)}): {termui.clip(nxt['title'], 50)}   "
                         "Enter = play   s = skip   q = stop  › ").strip().lower()
            if ans == "q":
                return
            if ans == "s":
                i += 1


# ======================================================================
# the dashboard's options
# ======================================================================
def search_and_play(cfg, query):
    results = search_youtube(query, n=20, cfg=cfg)
    if not results:
        print(paint("  No results.", C.GREY))
        return
    while True:
        show_results(results, query)
        raw = prompt("  Play which?   2   ·   1,3,5 (a queue)   ·   Enter = cancel  › ").strip()
        if not raw:
            return
        nums, problems = parse_selection(raw, len(results))
        if problems:
            print(paint("  Ignored: " + ", ".join(problems), C.AMBER))
        if not nums:
            print(paint("  Type a number like 2, or 1,3,5.", C.RED))
            continue
        play_queue(cfg, [results[i - 1] for i in nums])
        return


def _fmt_entry(i, h):
    pt = stream.resume_point(h)
    status = "✓ watched" if h.get("finished") else (f"{stream.clock(pt)} / {stream.clock(h.get('duration'))}" if pt else "")
    return (paint(f"{i:>3}  ", C.RED, C.BOLD) + paint(termui.clip(h.get("title", ""), 52).ljust(52), C.OFF)
            + "  " + paint(status.ljust(14), C.AMBER if pt else C.GREEN) + paint(h.get("watched_at", ""), C.GREY_D))


def continue_watching(cfg):
    items = stream.continue_list()
    if not items:
        print(paint("\n  Nothing to continue yet. Watch something and close VLC part-way.", C.GREY))
        return
    print()
    print(paint("  Continue watching", C.WHITE, C.BOLD))
    print()
    for i, h in enumerate(items, 1):
        print("  " + _fmt_entry(i, h))
    print()
    raw = prompt("  Which one?  number  ·  Enter = back  › ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(items):
        h = items[int(raw) - 1]
        play_one(cfg, h.get("url") or f"https://www.youtube.com/watch?v={h['id']}", auto_resume=True)


def history_menu(cfg):
    while True:
        hist = stream.load_history()
        if not hist:
            print(paint("\n  Your streaming history is empty.", C.GREY))
            return
        print()
        print(paint("  Streaming history", C.WHITE, C.BOLD) + paint(f"   {len(hist)} videos", C.GREY_D))
        print()
        for i, h in enumerate(hist[:25], 1):
            print("  " + _fmt_entry(i, h))
        print()
        raw = prompt("  number = play   d N = delete   c = clear all   Enter = back  › ").strip().lower()
        if not raw:
            return
        if raw == "c":
            if ask_yes_no("Clear the whole streaming history?", default=False):
                stream.clear()
            continue
        m = re.fullmatch(r"d\s*(\d+)", raw)
        if m and 1 <= int(m.group(1)) <= min(25, len(hist)):
            stream.remove(hist[int(m.group(1)) - 1].get("id") or hist[int(m.group(1)) - 1].get("url"))
            continue
        if raw.isdigit() and 1 <= int(raw) <= min(25, len(hist)):
            h = hist[int(raw) - 1]
            play_one(cfg, h.get("url") or f"https://www.youtube.com/watch?v={h['id']}")
            return
        print(paint("  Type a number, d N, c, or Enter.", C.AMBER))


def settings_menu(cfg):
    while True:
        vlc = stream.find_vlc(cfg)
        rows = [
            (1, "Quality cap", str(cfg.get("stream_quality", "1080")) + ("p" if cfg.get("stream_quality", "1080") != "best" else ""), "", False),
            (2, "Start fullscreen", "on" if cfg.get("stream_fullscreen") else "off", "", False),
            (3, "Offer to resume", "on" if cfg.get("stream_resume", True) else "off", "", False),
            (4, "VLC program", vlc or "not found", "", False),
            (5, "Extra VLC options", cfg.get("stream_args") or "none", "", False),
            (6, "Clear watch history", f"{len(stream.load_history())} videos", "", False),
            (7, "Back", "", "", False),
        ]
        print()
        pick = render_table("Streaming settings", rows, default_idx=7)
        if pick in (None, 7):
            return cfg
        if pick == 1:
            cur = cfg.get("stream_quality", "1080")
            q = render_table("Highest quality to stream",
                             [(i, name if name == "best" else f"{name}p", "", "", False)
                              for i, name in enumerate(stream.QUALITIES, 1)],
                             default_idx=stream.QUALITIES.index(cur) + 1 if cur in stream.QUALITIES else 2)
            cfg["stream_quality"] = stream.QUALITIES[q - 1]
        elif pick == 2:
            cfg["stream_fullscreen"] = ask_yes_no("Start VLC fullscreen?", default=bool(cfg.get("stream_fullscreen")))
        elif pick == 3:
            cfg["stream_resume"] = ask_yes_no("Offer to continue where you stopped?", default=cfg.get("stream_resume", True))
        elif pick == 4:
            cfg["stream_player"] = prompt("  Full path to VLC (Enter = auto-detect)  › ").strip()
        elif pick == 5:
            cfg["stream_args"] = prompt("  Extra VLC options, e.g. --aout=alsa (Enter = none)  › ").strip()
        elif pick == 6:
            if ask_yes_no("Clear the whole streaming history?", default=False):
                stream.clear()
        save_config(cfg)


def run_option(cfg, oid):
    if oid == "search":
        q = prompt("  Search YouTube for  › ").strip()
        if q:
            search_and_play(cfg, q)
    elif oid == "link":
        link = prompt("  Paste a YouTube link  › ").strip()
        if link:
            handle_text(cfg, link)
    elif oid == "continue":
        continue_watching(cfg)
    elif oid == "history":
        history_menu(cfg)
    elif oid == "channels":
        from .channels import channels_menu
        channels_menu(cfg, on_download=lambda picks: play_queue(cfg, picks))
    elif oid == "settings":
        cfg = settings_menu(cfg)
    return cfg


def handle_text(cfg, text):
    """A link plays; anything else is a search."""
    if looks_like_link(text):
        play_one(cfg, normalise_link(text))
    else:
        search_and_play(cfg, text)


# ======================================================================
# the screen and the loop
# ======================================================================
def recent_entries(cont, n=2):
    """History entries as the dashboard's "Continue watching" rows: ▶ title   12:34 / 45:10."""
    return [{"title": h.get("title", ""), "mark": "▶",
             "meta": f"{stream.clock(stream.resume_point(h))} / {stream.clock(h.get('duration'))}"}
            for h in cont[:n]]


def _dashboard(cfg):
    if not home.interactive():
        return None
    size = shutil.get_terminal_size((80, 24))
    budget = size.lines - (len(OPTIONS) + 12)
    style = "large" if budget >= 8 else "compact" if budget >= 5 else "minimal"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        banner(cfg, size=style, clear=False, max_rows=max(8, budget), word="STREAM", tagline=TAGLINE, saving=False)
    theme.clear_screen()
    sys.stdout.write(buf.getvalue())
    sys.stdout.flush()
    plain = [re.sub(r"\033\[[0-9;]*m", "", ln) for ln in buf.getvalue().splitlines()]
    frame = [len(ln) for ln in plain if ln.startswith(("╭", "│", "╰"))]
    cont = stream.continue_list()
    recent = recent_entries(cont)
    badges = {"continue": f"{len(cont)} to resume"} if cont else {}
    return home.run_home(cfg, badges, rows=max(6, size.lines - buf.getvalue().count("\n") - 1),
                         width=max(frame) if frame else None, options=OPTIONS, hints=HINTS,
                         recent=recent, recent_title="Continue watching")


def interactive_loop(cfg):
    while True:
        try:
            choice = _dashboard(cfg)
        except KeyboardInterrupt:
            print(paint("  Bye.", C.GREY))
            return 0
        try:
            if choice is None:                              # plain terminal / pipe
                text = prompt("  Search YouTube or paste a link  (q = quit)  › ").strip()
                if text.lower() in ("q", "quit", "exit", ""):
                    return 0
                handle_text(cfg, text)
                continue
            kind, value = choice
            if kind == "quit":
                print(paint("  Bye.", C.GREY))
                return 0
            if kind == "option":
                cfg = run_option(cfg, value)
            elif kind in ("link", "search", "command"):
                handle_text(cfg, value)
        except Back:
            print(paint("  ↩  Cancelled.", C.GREY))


def build_parser():
    p = argparse.ArgumentParser(prog="ytstream", description="Watch YouTube in VLC without downloading it.")
    p.add_argument("target", nargs="*", help="a YouTube link to play, or words to search for")
    p.add_argument("-q", "--quality", choices=list(stream.QUALITIES), help="highest quality for this run")
    p.add_argument("--fullscreen", action="store_true", help="start VLC fullscreen")
    p.add_argument("--no-resume", action="store_true", help="do not offer to resume")
    p.add_argument("--vlc", metavar="PATH", help="path to the VLC program")
    p.add_argument("-V", "--version", action="store_true")
    return p


def run(cfg, target=(), quality=None, fullscreen=False, no_resume=False, vlc=None):
    cfg = dict(cfg)                                             # command-line choices last for this run only
    if quality:
        cfg["stream_quality"] = quality
    if fullscreen:
        cfg["stream_fullscreen"] = True
    if no_resume:
        cfg["stream_resume"] = False
    if vlc:
        cfg["stream_player"] = vlc
    text = " ".join(target).strip()
    if text:
        try:
            handle_text(cfg, text)
        except Back:
            print(paint("  ↩  Cancelled.", C.GREY))
        return 0
    return interactive_loop(cfg)


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.version:
        from . import __version__
        print(f"ytstream (ytgrab) {__version__}")
        return 0
    try:
        return run(load_config(), args.target, args.quality, args.fullscreen, args.no_resume, args.vlc)
    except KeyboardInterrupt:
        print(paint("\n  Bye.", C.GREY))
        return 130


if __name__ == "__main__":
    sys.exit(main())
