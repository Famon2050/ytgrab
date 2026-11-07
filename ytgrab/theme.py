"""Colors, themes, banner for ytgrab."""

import os
import sys

W = 70


# Honour https://no-color.org and dumb terminals; colour only a real terminal
# (so `ytgrab sync >> log` stays clean) unless FORCE_COLOR is set.
def _color_enabled():
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("YTGRAB_FORCE_COLOR"):
        return True
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


_COLOR = _color_enabled()


def clear_screen():
    """Clear the terminal; does nothing when output is piped or redirected."""
    if not sys.stdout.isatty():
        return
    if os.name == "nt":
        os.system("cls")
    else:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def _rgb(r, g, b):
    return f"\033[38;2;{r};{g};{b}m" if _COLOR else ""


def _bg(r, g, b):
    return f"\033[48;2;{r};{g};{b}m" if _COLOR else ""


THEMES = {
    "youtube": {
        "RED": (203, 65, 65), "RED_HOT": (230, 100, 100), "AMBER": (221, 180, 60),
        "WHITE": (245, 245, 245), "OFF": (215, 215, 215), "GREY": (160, 160, 160),
        "GREY_D": (105, 105, 105), "BORDER": (75, 75, 75), "GREEN": (130, 200, 130),
        "BLUE": (90, 150, 220), "GRAD_A": (220, 40, 40), "GRAD_B": (40, 100, 220),
    },
    "nord": {
        "RED": (191, 97, 106), "RED_HOT": (208, 135, 112), "AMBER": (235, 203, 139),
        "WHITE": (236, 239, 244), "OFF": (216, 222, 233), "GREY": (143, 161, 179),
        "GREY_D": (76, 86, 106), "BORDER": (67, 76, 94), "GREEN": (163, 190, 140),
        "BLUE": (129, 161, 193), "GRAD_A": (191, 97, 106), "GRAD_B": (94, 129, 172),
    },
    "dracula": {
        "RED": (255, 85, 85), "RED_HOT": (255, 121, 198), "AMBER": (241, 250, 140),
        "WHITE": (248, 248, 242), "OFF": (248, 248, 242), "GREY": (189, 147, 249),
        "GREY_D": (98, 114, 164), "BORDER": (68, 71, 90), "GREEN": (80, 250, 123),
        "BLUE": (139, 233, 253), "GRAD_A": (255, 85, 85), "GRAD_B": (189, 147, 249),
    },
    "gruvbox": {
        "RED": (204, 36, 29), "RED_HOT": (251, 73, 52), "AMBER": (250, 189, 47),
        "WHITE": (251, 241, 199), "OFF": (235, 219, 178), "GREY": (168, 153, 132),
        "GREY_D": (102, 92, 84), "BORDER": (60, 56, 54), "GREEN": (184, 187, 38),
        "BLUE": (131, 165, 152), "GRAD_A": (204, 36, 29), "GRAD_B": (215, 153, 33),
    },
    "catppuccin": {
        "RED": (243, 139, 168), "RED_HOT": (235, 160, 172), "AMBER": (249, 226, 175),
        "WHITE": (205, 214, 244), "OFF": (205, 214, 244), "GREY": (166, 173, 200),
        "GREY_D": (108, 112, 134), "BORDER": (69, 71, 90), "GREEN": (166, 227, 161),
        "BLUE": (137, 180, 250), "GRAD_A": (243, 139, 168), "GRAD_B": (137, 180, 250),
    },
}

THEME_ORDER = ["youtube", "nord", "dracula", "gruvbox", "catppuccin"]


class C:
    R = "\033[0m" if _COLOR else ""; BOLD = "\033[1m" if _COLOR else ""
    DIM = "\033[2m" if _COLOR else ""
    RED = _rgb(203, 65, 65); RED_HOT = _rgb(230, 100, 100); AMBER = _rgb(221, 180, 60)
    WHITE = _rgb(245, 245, 245); OFF = _rgb(215, 215, 215); GREY = _rgb(160, 160, 160)
    GREY_D = _rgb(105, 105, 105); BORDER = _rgb(75, 75, 75); GREEN = _rgb(130, 200, 130)
    BLUE = _rgb(90, 150, 220); GRAD_A = (220, 40, 40); GRAD_B = (40, 100, 220)

CLR = "\033[K"


def apply_theme(name):
    t = THEMES.get(name) or THEMES["youtube"]
    for k, v in t.items():
        if k in ("GRAD_A", "GRAD_B"):
            setattr(C, k, v)
        else:
            setattr(C, k, _rgb(*v))


def paint(t, *s):
    return "".join(s) + str(t) + C.R


def _interp(a, b, n):
    if n <= 1:
        return [a]
    return [tuple(int(a[i] + (b[i] - a[i]) * k / (n - 1)) for i in range(3)) for k in range(n)]


def progress_palette(steps=6):
    return _interp(C.GRAD_A, C.GRAD_B, steps)


FIGLET = [
    "██╗   ██╗████████╗ ██████╗ ██████╗  █████╗ ██████╗ ",
    "╚██╗ ██╔╝╚══██╔══╝██╔════╝ ██╔══██╗██╔══██╗██╔══██╗",
    " ╚████╔╝    ██║   ██║  ███╗██████╔╝███████║██████╔╝",
    "  ╚██╔╝     ██║   ██║   ██║██╔══██╗██╔══██║██╔══██╗",
    "   ██║      ██║   ╚██████╔╝██║  ██║██║  ██║██║  ██║",
    "   ╚═╝      ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝",
]
TAGLINE = "youtube downloader"
AUTHOR = "fakrudin"


def _center_in(text, width):
    n = len(text)
    if n >= width:
        return text[:width]
    pad_l = (width - n) // 2
    return " " * pad_l + text + " " * (width - n - pad_l)


def _grad_text(text, a, b, bold=True, x0=0, span=None):
    """Colour `text` with a left-to-right gradient (spaces stay uncoloured).
    x0/span place it inside a wider gradient so rows line up."""
    span = span or max(len(text), 2)
    out = []
    for i, ch in enumerate(text):
        if ch == " ":
            out.append(ch); continue
        t = min(1.0, (x0 + i) / max(span - 1, 1))
        col = tuple(int(a[k] + (b[k] - a[k]) * t) for k in range(3))
        out.append((C.BOLD if bold else "") + _rgb(*col) + ch)
    return "".join(out) + C.R


def _vis(s):
    """Visible length of a string that contains ANSI colour codes."""
    import re
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


class _Frame:
    """Rounded frame whose border runs along the theme gradient."""

    def __init__(self, width):
        self.w = width
        self.inner = width - 2
        cols = _interp(C.GRAD_A, C.GRAD_B, width)
        self.cols = cols

    def _c(self, i, ch):
        return _rgb(*self.cols[i]) + ch + C.R

    def top(self):
        return (self._c(0, "╭") + "".join(self._c(i, "─") for i in range(1, self.w - 1))
                + self._c(self.w - 1, "╮"))

    def bottom(self):
        return (self._c(0, "╰") + "".join(self._c(i, "─") for i in range(1, self.w - 1))
                + self._c(self.w - 1, "╯"))

    def row(self, painted="", align="center", pad=2):
        n = _vis(painted)
        room = self.inner - n
        if align == "center":
            left = room // 2
        else:
            left = pad
        right = max(room - left, 0)
        return (self._c(0, "│") + " " * left + painted + " " * right
                + self._c(self.w - 1, "│"))


# ----------------------------------------------------------------------
# Big block-letter logo: a 5x7 pixel font drawn with half-block characters, so the
# letters scale with the terminal (and share its gradient) instead of being fixed.
# ----------------------------------------------------------------------
PIXEL_FONT = {
    "A": [".XXX.", "X...X", "X...X", "XXXXX", "X...X", "X...X", "X...X"],
    "B": ["XXXX.", "X...X", "X...X", "XXXX.", "X...X", "X...X", "XXXX."],
    "E": ["XXXXX", "X....", "X....", "XXXX.", "X....", "X....", "XXXXX"],
    "G": [".XXX.", "X...X", "X....", "X.XXX", "X...X", "X...X", ".XXX."],
    "M": ["X...X", "XX.XX", "X.X.X", "X.X.X", "X...X", "X...X", "X...X"],
    "R": ["XXXX.", "X...X", "X...X", "XXXX.", "X.X..", "X..X.", "X...X"],
    "S": [".XXXX", "X....", "X....", ".XXX.", "....X", "....X", "XXXX."],
    "T": ["XXXXX", "..X..", "..X..", "..X..", "..X..", "..X..", "..X.."],
    "Y": ["X...X", "X...X", ".X.X.", "..X..", "..X..", "..X..", "..X.."],
}
# (name, pixel width in columns, pixel height in half-rows), biggest first
LOGO_SIZES = (("xl", 3, 2), ("large", 2, 2), ("wide", 2, 1))
_CHROME_ROWS = 4            # frame top, spacer, tagline, frame bottom


def _bitmap(word):
    rows = [""] * 7
    for i, ch in enumerate(word.upper()):
        glyph = PIXEL_FONT.get(ch, ["....."] * 7)          # unknown character: a blank cell
        for r in range(7):
            rows[r] += glyph[r] + ("." if i < len(word) - 1 else "")
    return rows


def _shadow_offset(sx, sy):
    return max(1, (sx + 1) // 2), max(1, sy // 2)


def logo_dims(word, sx, sy, shadow=True):
    """(columns, text rows) the logo occupies."""
    w = len(_bitmap(word)[0]) * sx
    dx, dy = _shadow_offset(sx, sy) if shadow else (0, 0)
    return w + dx, (7 * sy + dy + 1) // 2


def render_logo(word, sx, sy, shadow=True):
    """The word in big gradient letters with a soft drop shadow. Returns text rows."""
    bm = _bitmap(word)
    pw, ph = len(bm[0]), 7
    W, H = pw * sx, ph * sy
    shadow = shadow and _COLOR              # without colour a shadow cannot be told from the letter
    dx, dy = _shadow_offset(sx, sy) if shadow else (0, 0)
    rst = C.R if _COLOR else ""
    cols = _interp(C.GRAD_A, C.GRAD_B, W)

    def lit(x, y):
        return 0 <= x < W and 0 <= y < H and bm[y // sy][x // sx] == "X"

    def state(x, y):
        if lit(x, y):
            base = cols[x]
            k = 1.12 - 0.34 * (y / max(H - 1, 1))          # light from above
            return "f", tuple(max(0, min(255, int(c * k))) for c in base)
        if shadow and lit(x - dx, y - dy):
            c = cols[min(max(x - dx, 0), W - 1)]
            return "s", tuple(int(v * 0.30) for v in c)
        return None

    out = []
    for r in range((H + dy + 1) // 2):
        line, last = [], None
        for x in range(W + dx):
            top, bot = state(x, 2 * r), state(x, 2 * r + 1)
            if top is None and bot is None:
                cell, style = " ", None
            elif top is not None and bot is not None:
                if top == bot or not _COLOR:
                    cell, style = "█", (top[1], None)
                else:
                    cell, style = "▀", (top[1], bot[1])
            elif top is not None:
                cell, style = "▀", (top[1], None)
            else:
                cell, style = "▄", (bot[1], None)
            if style is None:
                if last is not None:
                    line.append(rst)
                    last = None
                line.append(" ")
                continue
            if style != last:
                fg, bg = style
                line.append(rst + _rgb(*fg) + (_bg(*bg) if bg else ""))
                last = style
            line.append(cell)
        if last is not None:
            line.append(rst)
        out.append("".join(line))
    return out


def pick_logo_size(word, cols, max_rows):
    """The biggest logo that fits `cols` x `max_rows` (frame included), or None."""
    for name, sx, sy in LOGO_SIZES:
        w, rows = logo_dims(word, sx, sy, shadow=_COLOR)
        if w + 8 <= cols - 1 and rows + _CHROME_ROWS <= max_rows:
            return name, sx, sy
    return None


def _big(cfg, w, logo_word, sx, sy, tagline=None):
    from . import __version__
    f = _Frame(w)
    lines = [f.top(), f.row()]
    for row in render_logo(logo_word, sx, sy):
        lines.append(f.row(row))
    tag = (paint("▶", C.RED_HOT, C.BOLD) + " " + paint(tagline or TAGLINE, C.OFF)
           + paint("   ·   by ", C.GREY_D) + paint(AUTHOR, C.RED_HOT, C.BOLD)
           + paint(f"   ·   v{__version__}", C.GREY_D))
    lines.append(f.row(tag))
    lines.append(f.bottom())
    return lines


def _banner_width(size):
    """Width to draw at, and the style actually used (falls back on narrow terminals)."""
    import shutil
    cols = shutil.get_terminal_size((80, 24)).columns
    if size == "large" and cols < 58:
        size = "compact"
    if size == "compact" and cols < 46:
        size = "minimal"
    return max(20, min(W, cols - 1)), size


def _classic(cfg, w):
    f = _Frame(w)
    from . import __version__
    logo_w = len(FIGLET[0])
    lines = [f.top(), f.row()]
    for r, line in enumerate(FIGLET):
        # offset each row slightly so the gradient shimmers diagonally
        painted = _grad_text(line, C.GRAD_A, C.GRAD_B, True, x0=r * 2, span=logo_w + 12)
        lines.append(f.row(painted))
    lines.append(f.row())
    lines.append(f.row(paint("▶", C.RED_HOT, C.BOLD) + "  " + paint(TAGLINE, C.OFF)))
    rule = paint("──────  ", C.BORDER) + paint("◆", C.RED_HOT) + paint("  ──────", C.BORDER)
    lines.append(f.row(rule))
    credit = (paint("made by ", C.GREY_D) + paint(AUTHOR, C.RED_HOT, C.BOLD)
              + paint(f"   ·   v{__version__}", C.GREY_D))
    lines.append(f.row(credit))
    lines.append(f.row())
    lines.append(f.bottom())
    return lines


def _compact(cfg, w):
    f = _Frame(w)
    from . import __version__
    left = (paint("▶", C.RED_HOT, C.BOLD) + " " + _grad_text("ytgrab", C.GRAD_A, C.GRAD_B)
            + "  " + paint("·", C.BORDER) + "  " + paint(TAGLINE, C.GREY))
    right = paint(f"by {AUTHOR}", C.GREY_D) + paint(f"  v{__version__}", C.BORDER)
    gap = max(f.inner - 2 - _vis(left) - _vis(right) - 2, 1)
    body = " " + left + " " * gap + right + " "
    return [f.top(), f.row(body, align="left", pad=1), f.bottom()]


def _minimal(cfg, w):
    from . import __version__
    from .fmt import pretty_dir
    left = paint("▶", C.RED_HOT, C.BOLD) + " " + paint("ytgrab", C.WHITE, C.BOLD) + " "
    right = " " + paint(pretty_dir(cfg["download_dir"]), C.GREY) + paint(f"  v{__version__}", C.GREY_D)
    room = max(w - _vis(left) - _vis(right), 4)
    rule = "".join(_rgb(*c) + "━" for c in _interp(C.GRAD_A, C.GRAD_B, room)) + C.R
    return [left + rule + right]


def banner(cfg, size=None, clear=True, max_rows=None, word="YTGRAB", tagline=None, saving=True):
    """Print the banner. size: large | classic | compact | minimal (default: setting).

    "large" picks the biggest block-letter logo that fits the terminal (width AND
    height, so the menu below still has room) and falls back step by step:
    large -> classic -> compact -> minimal."""
    import shutil
    if clear:
        clear_screen()
    if sys.stdout.isatty():
        sys.stdout.write("\033]0;ytgrab\007")
    size = size or cfg.get("banner_size", "large")
    if size not in ("large", "classic", "compact", "minimal"):
        size = "large"
    term = shutil.get_terminal_size((80, 24))
    if max_rows is None:
        max_rows = max(8, term.lines - 22)
    lines = None
    w, eff = _banner_width("large" if size in ("large", "classic") else size)
    if size == "large" and eff == "large":
        pick = pick_logo_size(word, term.columns, max_rows)
        if pick:
            _, sx, sy = pick
            lw, _rows = logo_dims(word, sx, sy, shadow=_COLOR)
            w = min(term.columns - 1, lw + 8)
            lines = _big(cfg, w, word, sx, sy, tagline)
            eff = "big"
    if lines is None:
        lines = {"large": _classic, "compact": _compact, "minimal": _minimal}[eff](cfg, w)
    print()
    for ln in lines:
        print(ln)
    if eff != "minimal" and saving:
        from .fmt import pretty_dir
        print(paint(_center_in(f"saving to  {pretty_dir(cfg['download_dir'])}", w), C.GREY_D))
    print()
