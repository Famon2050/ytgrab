"""Small terminal helpers shared by the interactive screens (channels, ytstream)."""

import contextlib
import re
import shutil
import sys

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def spinner(t):
    return SPIN[int(t * 12) % len(SPIN)]


def size():
    s = shutil.get_terminal_size((80, 24))
    return s.columns, s.lines


@contextlib.contextmanager
def alt_screen(out=None):
    """Use the terminal's alternate screen: the user's scrollback comes back untouched afterwards."""
    out = out or sys.stdout
    try:
        out.write("\033[?1049h\033[?25l\033[2J\033[H")
        out.flush()
    except (OSError, ValueError):
        pass
    try:
        yield
    finally:
        try:
            out.write("\033[?25h\033[?1049l")
            out.flush()
        except (OSError, ValueError):
            pass


def draw_full(lines, out=None, rows=None):
    """Repaint the whole (alternate) screen: first `rows` lines, each cleared to its end."""
    out = out or sys.stdout
    rows = rows or size()[1]
    body = "\033[H" + "\n".join(ln + "\033[K" for ln in lines[:rows]) + "\033[J"
    try:
        out.write(body)
        out.flush()
    except (OSError, ValueError):
        pass


def clip(text, width):
    """Plain text cut to `width` cells with an ellipsis."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[:width] if width == 1 else text[:width - 1] + "…"


_SGR = re.compile(r"\x1b\[[0-9;]*m")


def clip_ansi(text, width):
    """Cut a string that contains colour codes to `width` visible cells (codes are kept intact)."""
    if width <= 0:
        return ""
    out, vis, i = [], 0, 0
    while i < len(text):
        m = _SGR.match(text, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        if vis >= width:
            break
        out.append(text[i])
        vis += 1
        i += 1
    s = "".join(out)
    return s + "\033[0m" if "\033[" in s and not s.endswith("\033[0m") else s
