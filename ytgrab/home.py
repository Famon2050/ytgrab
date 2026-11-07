"""The home dashboard of ytgrab: input box with a live placeholder + a menu of options.

    ╭──────────────────────────────────────────────────────────╮
    │ ❯  Search YouTube, paste a link, or pick an option…      │
    ╰──────────────────────────────────────────────────────────╯
      ▸ 1  Download a link     paste a video or playlist URL
        2  Search YouTube      find videos, pick one or many
        ...

Typing goes into the box (a link downloads, any other text searches, a number opens that option);
the arrow keys move through the options when the box is empty. Everything is drawn by pure functions
(`render_home`) so it can be tested without a terminal; `run_home` is the thin interactive loop.
"""

import shutil
import sys
import time

from . import termui, theme
from .keys import open_keys
from .theme import C, paint

OPTIONS = (
    {"id": "download", "label": "Download a link", "desc": "paste a video or playlist URL",
     "aliases": ("download", "link", "url", "get")},
    {"id": "search", "label": "Search YouTube", "desc": "find videos, pick one or many (1,3,5-7 or all)",
     "aliases": ("search",)},
    {"id": "channels", "label": "My channels", "desc": "saved creators: videos, shorts, live, playlists, podcasts",
     "aliases": ("channels", "channel", "creators", "subscriptions", "subs")},
    {"id": "stream", "label": "Stream in VLC", "desc": "watch without downloading (also: ytstream)",
     "aliases": ("stream", "ytstream", "vlc", "watch video")},
    {"id": "queue", "label": "Pending queue", "desc": "links waiting to be downloaded",
     "aliases": ("queue", "pending")},
    {"id": "watch", "label": "Watch clipboard", "desc": "queue links as you copy them",
     "aliases": ("watch", "clipboard")},
    {"id": "library", "label": "Library", "desc": "find files, remove duplicates, stats, history",
     "aliases": ("library", "files", "stats")},
    {"id": "settings", "label": "Settings", "desc": "quality, folders, banner, bar style, cookies…",
     "aliases": ("settings", "config")},
    {"id": "update", "label": "Update ytgrab", "desc": "check, install, preview, roll back",
     "aliases": ("update", "upgrade", "self-update")},
)

HINTS = (
    "Search YouTube, paste a link, or choose an option…",
    "Paste a video or playlist link…",
    "Type a number to open an option…",
    "Try a search:  lofi hip hop radio",
    "Use ↑ ↓ to browse the options, Enter to open…",
)
_DIM, _SOFT = (92, 96, 108), (150, 154, 166)
_HINT_SECONDS = 3.6


# ----------------------------------------------------------------------
# line editor (pure)
# ----------------------------------------------------------------------
class LineEditor:
    """One line of text with a cursor. ``apply(token)`` returns "submit", "eof", "escape" or None."""

    def __init__(self, text=""):
        self.text, self.pos = text, len(text)

    def clear(self):
        self.text, self.pos = "", 0

    def apply(self, tok):
        t, p = self.text, self.pos
        if tok == "ENTER":
            return "submit"
        if tok == "CTRL_D":
            if not t:
                return "eof"
            self.text = t[:p] + t[p + 1:]
        elif tok == "ESC":
            self.clear()
            return "escape"
        elif tok == "BACKSPACE":
            if p:
                self.text, self.pos = t[:p - 1] + t[p:], p - 1
        elif tok == "DELETE":
            self.text = t[:p] + t[p + 1:]
        elif tok == "LEFT":
            self.pos = max(0, p - 1)
        elif tok == "RIGHT":
            self.pos = min(len(t), p + 1)
        elif tok in ("HOME", "CTRL_A"):
            self.pos = 0
        elif tok in ("END", "CTRL_E"):
            self.pos = len(t)
        elif tok == "CTRL_U":
            self.text, self.pos = t[p:], 0
        elif tok == "CTRL_W":
            head = t[:p].rstrip()
            cut = head.rfind(" ") + 1
            self.text, self.pos = t[:cut] + t[p:], cut
        elif len(tok) == 1 and tok >= " ":
            self.text, self.pos = t[:p] + tok + t[p:], p + 1
        return None


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def classify(text, options=OPTIONS):
    """What pressing Enter on `text` would do: ("empty"|"quit"|"option"|"link"|"search"|"command", value)."""
    s = (text or "").strip()
    if not s:
        return "empty", None
    low = s.lower()
    if s == "q" or low in ("quit", "exit"):          # a capital Q is the queue command, not "quit"
        return "quit", None
    if s.isdigit() and 1 <= int(s) <= len(options):
        return "option", options[int(s) - 1]["id"]
    for opt in options:
        if low in opt["aliases"]:
            return "option", opt["id"]
    if s.startswith(("http://", "https://")):
        return "link", s
    if len(s) == 1 or low in ("settings", "pending", "watch", "stats", "find", "dupes"):
        return "command", s
    return "search", s


def ago(stamp, now=None):
    """"2h ago" for a history timestamp like 2026-10-14 12:30; "" if unreadable."""
    try:
        then = time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M"))
    except (TypeError, ValueError):
        return ""
    sec = max(0, (time.time() if now is None else now) - then)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if sec >= size:
            return f"{int(sec // size)}{unit} ago"
    return "just now"


def _clip(text, width):
    if width <= 0:
        return ""
    return text if len(text) <= width else (text[:width] if width == 1 else text[:width - 1] + "…")


def _mix(a, b, k):
    k = max(0.0, min(1.0, k))
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def hint_at(now, hints=None):
    """(text, brightness 0..1): the placeholder that is showing now and how visible it is."""
    hints = hints or HINTS
    i = int(now / _HINT_SECONDS) % len(hints)
    phase = (now % _HINT_SECONDS) / _HINT_SECONDS
    fade = min(1.0, phase / 0.10, (1.0 - phase) / 0.10)           # fade in / out at the ends
    return hints[i], max(0.0, fade)


class HomeState:
    def __init__(self, options=OPTIONS, badges=None, recent=None, hints=None, recent_title="Recently downloaded"):
        self.options = options
        self.hints = hints or HINTS
        self.recent_title = recent_title
        self.badges = badges or {}
        self.recent = recent or []
        self.editor = LineEditor()
        self.selected = None


# ----------------------------------------------------------------------
# rendering (pure)
# ----------------------------------------------------------------------
def render_home(state, cols, rows, now, width=None):
    """-> (lines, (cursor_row, cursor_col)). Every line is at most cols-1 cells wide and the
    block is at most `rows` lines tall (it drops detail until it fits)."""
    avail = max(10, cols - 1)
    bw = max(10, min(avail - (0 if width else 4), width if width else 100))     # box width = banner width
    box_pad = "" if width else "  "
    color = bool(theme._COLOR)
    text, pos = state.editor.text, state.editor.pos
    kind, value = classify(text, state.options)
    inner = bw - 6                                     # "│ ❯ " + " │"
    pad = "  "

    # --- input box
    active = bool(text)
    edge = C.RED_HOT if active else C.BORDER
    start = max(0, pos - (inner - 1)) if len(text) >= inner else 0
    shown = text[start:start + inner]
    cursor_col = len(box_pad) + 4 + (pos - start)
    if text:
        body = paint(shown, C.WHITE, C.BOLD)
        used = len(shown)
        tag = {"link": "link  ⏎ download", "search": "search  ⏎", "option": "", "command": "command  ⏎",
               "quit": "quit  ⏎"}.get(kind, "")
        if kind == "option":
            opt = next(o for o in state.options if o["id"] == value)
            tag = f"→ {opt['label']}  ⏎"
        if tag and inner - used > len(tag) + 2:
            body += " " * (inner - used - len(tag)) + paint(tag, C.GREY_D)
            used = inner
    else:
        hint, vis = hint_at(now, state.hints)
        hint = _clip(hint, inner)
        if color:
            r, g, b = _mix(_DIM, _SOFT, vis * 0.35 + 0.1)
            body = theme._rgb(int(r), int(g), int(b)) + hint + C.R
        else:
            body = hint
        used = len(hint)
    mid = (box_pad + paint("│", edge) + " " + paint("❯ ", C.RED_HOT, C.BOLD) + body
           + " " * max(0, inner - used) + " " + paint("│", edge))
    top = box_pad + paint("╭" + "─" * (bw - 2) + "╮", edge)
    bottom = box_pad + paint("╰" + "─" * (bw - 2) + "╯", edge)

    # --- options
    label_w = max(len(o["label"]) for o in state.options) + 2
    sel = state.selected
    if kind == "option":
        sel = next(i for i, o in enumerate(state.options) if o["id"] == value)
    option_lines = []
    for i, o in enumerate(state.options):
        badge = state.badges.get(o["id"])
        badge_txt = f"  {badge}" if badge else ""
        room = avail - (2 + 2 + 3 + label_w) - len(badge_txt) - 1
        desc = _clip(o["desc"], room) if room > 6 else ""
        on = (i == sel)
        marker = paint("▸ ", C.RED_HOT, C.BOLD) if on else "  "
        num = paint(f"{i + 1}", C.RED_HOT if on else C.RED, C.BOLD)
        label = paint(o["label"].ljust(label_w), C.WHITE, C.BOLD) if on else paint(o["label"].ljust(label_w), C.OFF)
        line = pad + marker + num + "  " + label + paint(desc, C.GREY if on else C.GREY_D)
        if badge:
            line += paint(badge_txt, C.AMBER, C.BOLD)
        if on and color:
            width = len(pad) + 2 + 1 + 2 + label_w + len(desc) + len(badge_txt)
            fill = " " * max(0, min(avail, bw + len(box_pad)) - width)
            hl = theme._bg(34, 36, 48)
            line = hl + line.replace(C.R, C.R + hl) + fill + C.R
        option_lines.append(line)
    heading = pad + paint("What would you like to do?", C.GREY)
    hint_full = pad + paint("↑↓ choose   Enter open   type to search or paste a link   Ctrl+C quit", C.GREY_D)
    hint_short = pad + paint("↑↓ choose · Enter open · Ctrl+C quit", C.GREY_D)
    hint_line = hint_full if plain_len(hint_full) <= avail else hint_short

    recent = []
    for entry in state.recent[:2]:
        title = _clip(str(entry.get("title", "")), max(8, min(avail, bw + 2) - 24))
        when = entry.get("meta") or ago(entry.get("date", ""))
        recent.append(pad + paint(entry.get("mark", "✓") + " ", C.GREEN) + paint(title, C.GREY)
                      + (paint("   " + when, C.GREY_D) if when else ""))

    def assemble(level):
        lines = [mid] if level >= 5 else [top, mid, bottom]
        if level <= 2:
            lines.append("")
        if level <= 3:
            lines.append(heading)
        lines += option_lines
        if level <= 1 and recent:
            lines += ["", pad + paint(state.recent_title, C.GREY_D)] + recent
        if level <= 2:
            lines.append("")
        if level <= 4:
            lines.append(hint_line)
        return lines

    level = 0
    lines = assemble(0)
    while len(lines) > rows and level < 5:
        level += 1
        lines = assemble(level)
    lines = [termui.clip_ansi(ln, avail) for ln in lines[:max(1, rows)]]   # tiny window: keep the input, clip the rest
    return lines, (0 if level >= 5 else 1, min(cursor_col, avail))


def plain_len(s):
    """Visible width of a string that contains colour codes."""
    return theme._vis(s)


# ----------------------------------------------------------------------
# interactive loop
# ----------------------------------------------------------------------
def interactive():
    import os
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def recent_downloads(n=2):
    try:
        from .config import load_history
        hist = [h for h in load_history() if isinstance(h, dict) and h.get("title")]
    except Exception:
        return []
    return hist[-n:][::-1]


def run_home(cfg, badges=None, reader="auto", out=None, clock=time.monotonic, rows=None, options=OPTIONS,
             width=None, hints=None, recent=None, recent_title="Recently downloaded"):
    """Show the dashboard and wait for a choice.
    Returns ("option", id) | ("link"|"search"|"command", text) | ("quit", None), or None when this
    is not an interactive terminal (the caller then falls back to a plain prompt)."""
    if reader == "auto":
        reader = open_keys()
    if reader is None:
        return None
    out = out or sys.stdout
    state = HomeState(options, badges, recent_downloads() if recent is None else recent, hints, recent_title)
    t0 = clock()
    cursor_row = 0
    nlines = 0
    first = True

    def write(s):
        try:
            out.write(s)
            out.flush()
        except (OSError, ValueError):
            pass

    def draw():
        nonlocal cursor_row, first, nlines
        size = shutil.get_terminal_size((80, 24))
        limit = rows if rows is not None else max(6, size.lines - 1)
        lines, (crow, ccol) = render_home(state, size.columns, limit, clock() - t0, width)
        buf = [] if first else [f"\033[{cursor_row}A" if cursor_row else "", "\r"]
        buf.append("\033[?25l")
        buf.append("\n".join(ln + "\033[K" for ln in lines))
        buf.append("\033[J")
        up = len(lines) - 1 - crow
        buf.append(f"\033[{up}A" if up else "")
        buf.append("\r" + (f"\033[{ccol}C" if ccol else "") + "\033[?25h")
        write("".join(buf))
        cursor_row, first, nlines = crow, False, len(lines)

    try:
        draw()
        while True:
            try:
                tok = reader.read(0.08)
            except KeyboardInterrupt:
                tok = "CTRL_C"
            if tok == "CTRL_C":
                return ("quit", None)
            if tok is None:
                if not state.editor.text:              # keep the placeholder animating
                    draw()
                continue
            ed = state.editor
            if tok in ("UP", "DOWN", "TAB", "BTAB") and not ed.text:
                n = len(state.options)
                step = -1 if tok in ("UP", "BTAB") else 1
                state.selected = (n - 1 if step < 0 else 0) if state.selected is None else (state.selected + step) % n
                draw()
                continue
            result = ed.apply(tok)
            if ed.text:
                state.selected = None
            if result == "eof":
                return ("quit", None)
            if result == "submit":
                kind, value = classify(ed.text, state.options)
                if kind == "empty":
                    if state.selected is not None:
                        return ("option", state.options[state.selected]["id"])
                    draw()
                    continue
                if kind == "quit":
                    return ("quit", None)
                if kind == "option":
                    return ("option", value)
                return (kind, ed.text.strip())
            draw()
    finally:
        reader.close()
        write("\033[?25h")
        # leave the cursor below the block so whatever comes next starts on a fresh line
        below = max(0, nlines - 1 - cursor_row)
        write(("\033[%dB" % below if below else "") + "\r\n")
