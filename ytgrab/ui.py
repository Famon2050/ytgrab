"""Interactive UI: prompts, tables, spinner, progress, info card."""

import sys
import threading
import time

from .theme import C, CLR, W, paint
from .fmt import fmt_date, fmt_duration, fmt_views


class Back(Exception):
    """Raised when the user presses Ctrl+C at a prompt: go back one level
    instead of killing the whole program."""


def prompt(msg):
    try:
        return input(paint(msg, C.WHITE)).strip()
    except KeyboardInterrupt:
        print()
        raise Back() from None
    except EOFError:
        print(paint("\nBye.", C.GREY)); sys.exit(0)


def ask_yes_no(msg, default=False):
    hint = "[Y/n]" if default else "[y/N]"
    raw = prompt(f"  {msg}  {hint}  › ").lower()
    return default if not raw else raw in ("y", "yes")


_COL_NUM = 5; _COL_NAME = 40; _COL_SIZE = 12


def render_table(title, rows, default_idx=None):
    print(paint("  " + title, C.WHITE, C.BOLD)); print()
    hdr = ("  " + " " * _COL_NUM + paint("Name".ljust(_COL_NAME), C.GREY_D)
           + paint("Size".rjust(_COL_SIZE + 2), C.GREY_D))
    print(hdr)
    print("  " + paint("─" * (_COL_NUM + _COL_NAME + _COL_SIZE + 2), C.BORDER))
    for idx, name, detail, size_str, unavailable in rows:
        num = paint(f"{idx:>2}.", C.RED, C.BOLD)
        num_pad = " " * (_COL_NUM - len(f"{idx:>2}."))
        if unavailable:
            name_txt = paint(f"{name}  (unavailable)".ljust(_COL_NAME), C.GREY_D)
            size_txt = paint("—".rjust(_COL_SIZE), C.GREY_D); marker = ""
        else:
            name_plain = f"{name}  ·  {detail}" if detail else name
            if len(name_plain) > _COL_NAME - 1:
                name_plain = name_plain[: _COL_NAME - 2] + "…"
            name_txt = paint(name_plain.ljust(_COL_NAME), C.OFF)
            size_txt = paint(size_str.rjust(_COL_SIZE), C.AMBER)
            marker = "  " + paint("★", C.AMBER) if default_idx and idx == default_idx else ""
        print("  " + num + num_pad + " " + name_txt + "  " + size_txt + marker)
    print()
    hint = f"  Choose [1-{len(rows)}"
    if default_idx: hint += f", Enter={default_idx}"
    hint += "]  › "
    while True:
        raw = prompt(hint)
        if raw == "" and default_idx: return default_idx
        if raw.isdigit() and 1 <= int(raw) <= len(rows):
            choice = int(raw)
            if rows[choice - 1][4]:
                print(paint("  That option isn't available.", C.RED)); continue
            return choice
        print(paint("  Invalid choice, try again.", C.RED))


class Spinner:
    def __init__(self, msg="Loading"):
        self.msg = msg; self._running = False; self._thread = None

    def _spin(self):
        frames = "⣾⣽⣻⢿⡿⣟⣯⣷"; i = 0
        while self._running:
            line = paint("  " + frames[i % len(frames)], C.RED) + " " + paint(self.msg + "…", C.GREY)
            sys.stdout.write("\r" + line + CLR); sys.stdout.flush()
            i += 1; time.sleep(0.08)
        sys.stdout.write("\r" + CLR); sys.stdout.flush()

    def start(self):
        if not sys.stdout.isatty():        # piped / cron: no animation frames in the log
            return
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread: self._thread.join()


def _card_top(title):
    head = f"─ {title} "; n = W - 4 - len(head)
    print(paint("  ╭" + head + "─" * n + "╮", C.BORDER))

def _card_bot():
    print(paint("  ╰" + "─" * (W - 4) + "╯", C.BORDER))

def _card_blank():
    print(paint("  │", C.BORDER) + " " * (W - 4) + paint("│", C.BORDER))

def _card_line(content, vis):
    pad = " " * max(0, (W - 4) - vis - 1)
    print(paint("  │", C.BORDER) + " " + content + pad + paint("│", C.BORDER))


def show_info(info):
    _card_top("Now playing"); _card_blank()
    title = str(info.get("title", "?"))
    if len(title) > 54: title = title[:53] + "…"
    line = "  " + paint("▶", C.RED_HOT, C.BOLD) + "  " + paint(title, C.WHITE, C.BOLD)
    _card_line(line, 5 + len(title)); _card_blank()
    rows = [
        ("Channel",  info.get("uploader") or info.get("channel") or "?"),
        ("Duration", fmt_duration(info.get("duration"))),
        ("Views",    fmt_views(info.get("view_count"))),
        ("Uploaded", fmt_date(info.get("upload_date"))),
    ]
    for lbl, val in rows:
        val = str(val)
        line = ("  " + paint("●", C.RED) + "  "
                + paint(f"{lbl:<10}", C.GREY) + paint(val, C.OFF))
        _card_line(line, 15 + len(val))
    _card_blank(); _card_bot(); print()
