"""The download progress display: ONE animated bar and nothing else.

* One line, always. Its width is recomputed every frame from the real terminal
  size, so it can never wrap, however narrow (or wide) the window is, and it
  follows live resizes.
* Video + audio streams are folded into a single bar that only moves forward.
* Sub-cell precision (eighth blocks), a gradient from the theme, a light that
  sweeps across the filled part, a bouncing pulse while the size is unknown and
  a "flow" while ffmpeg merges/converts at the end. Everything is eased, so the
  bar glides instead of jumping.
* Piped / cron / no-colour: no animation; a plain `  40%` line every 10 %.

The renderer is a normal yt-dlp progress hook (``renderer.hook``) plus
``start()`` / ``finish()`` / ``close()`` that the caller uses to bracket a download.
"""

import math
import re
import shutil
import sys
import threading
import time
from collections import deque

from . import theme
from .fmt import fmt_size

FPS = 24
MAX_BAR = 64                 # a bar wider than this just looks stretched
MIN_BAR = 6                  # narrower than this we fall back to "42%" only
_LABEL_W = 5                 # " 100%" / " ⠹   "
_TRACK = (46, 46, 56)
_WHITE = (255, 255, 255)
_GREEN_A, _GREEN_B = (36, 140, 84), (120, 232, 158)
_PARTIAL = " ▏▎▍▌▋▊▉"        # 0..7 eighths of a cell
_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Looks the user can pick (Settings -> Progress bar style, or `ytgrab bar-style`).
# stops=None means "use the current theme's gradient".  anim picks the motion.
STYLES = {
    "classic": {"label": "Classic", "desc": "your theme's colours, a light sweeping across",
                "stops": None, "anim": "sweep", "kind": "block"},
    "fire":    {"label": "Fire", "desc": "glowing embers that flicker",
                "stops": [(150, 10, 0), (255, 70, 0), (255, 170, 20), (255, 240, 120)],
                "anim": "flicker", "kind": "block"},
    "ocean":   {"label": "Ocean", "desc": "a slow wave rolling through the water",
                "stops": [(0, 40, 120), (0, 120, 210), (0, 200, 235), (170, 255, 240)],
                "anim": "wave", "kind": "block"},
    "aurora":  {"label": "Aurora", "desc": "northern lights drifting along the bar",
                "stops": [(90, 40, 220), (0, 200, 190), (110, 255, 140), (0, 200, 190), (90, 40, 220)],
                "anim": "flow", "kind": "block"},
    "neon":    {"label": "Neon", "desc": "a pulsing pink-to-cyan glow",
                "stops": [(255, 0, 140), (170, 60, 255), (0, 200, 255)],
                "anim": "pulse", "kind": "block"},
    "sunset":  {"label": "Sunset", "desc": "warm and calm",
                "stops": [(255, 80, 110), (255, 140, 80), (255, 210, 110)],
                "anim": "sweep", "kind": "block"},
    "matrix":  {"label": "Matrix", "desc": "green rain falling through the bar",
                "stops": [(0, 70, 20), (0, 200, 70), (170, 255, 180)],
                "anim": "rain", "kind": "block"},
    "minimal": {"label": "Minimal", "desc": "a thin line, nothing else",
                "stops": None, "anim": "none", "kind": "line"},
}
STYLE_ORDER = list(STYLES)
DEFAULT_STYLE = "classic"


def _gradient(stops, n):
    """`n` colours blended through all of `stops`."""
    if n <= 1:
        return [stops[0]]
    out = []
    for i in range(n):
        x = i / (n - 1) * (len(stops) - 1)
        j = min(int(x), len(stops) - 2)
        out.append(_mix(stops[j], stops[j + 1], x - j))
    return out


def visible_len(s):
    """Printed width of a string that may contain ANSI escapes."""
    return len(_ANSI.sub("", s))


def _mix(a, b, k):
    k = max(0.0, min(1.0, k))
    return (int(a[0] + (b[0] - a[0]) * k), int(a[1] + (b[1] - a[1]) * k),
            int(a[2] + (b[2] - a[2]) * k))


def _lerp_palette(a, b, n):
    if n <= 1:
        return [a]
    return [_mix(a, b, i / (n - 1)) for i in range(n)]


def _paint(fg=None, bg=None, bold=False):
    parts = []
    if bold:
        parts.append("1")
    if fg:
        parts.append(f"38;2;{fg[0]};{fg[1]};{fg[2]}")
    if bg:
        parts.append(f"48;2;{bg[0]};{bg[1]};{bg[2]}")
    return f"\033[{';'.join(parts)}m" if parts else ""


def _ease(p):
    return p * p * (3 - 2 * p)


class _Stream:
    __slots__ = ("kind", "frac")

    def __init__(self, kind):
        self.kind = kind
        self.frac = None             # None = size not known yet


class ProgressRenderer:
    """yt-dlp progress hook that draws a single adaptive, animated bar."""

    def __init__(self, stream=None, style=DEFAULT_STYLE, details=True):
        self.details = details              # show speed + ETA next to the bar
        self.style_name = style if style in STYLES else DEFAULT_STYLE
        self.style = STYLES[self.style_name]
        self._stream_override = stream
        self._lock = threading.RLock()
        self._streams = {}           # filename -> _Stream (insertion ordered)
        self._phase = "wait"         # wait -> download -> processing -> done
        self._target = None          # 0..1, None while unknown
        self._shown = 0.0            # eased value that is actually drawn
        self._t0 = time.monotonic()
        self._last_t = self._t0
        self._thread = None
        self._stop = threading.Event()
        self._started = False
        self._closed = False
        self._last_len = 0
        self._last_cols = None
        self._last_milestone = 0
        self._clock = time.monotonic        # replaceable in tests
        self._speed = None                  # smoothed bytes/s of the stream in flight
        self._speed_at = 0.0
        self._samples = deque(maxlen=80)    # (time, overall fraction) for the ETA
        self._started_at = None

    # ------------------------------------------------------------------ io
    @property
    def _out(self):
        return self._stream_override or sys.stdout

    def _tty(self):
        try:
            return self._out.isatty()
        except (AttributeError, ValueError):
            return False

    @staticmethod
    def _cols():
        return max(1, shutil.get_terminal_size((80, 24)).columns)

    # --------------------------------------------------------------- state
    @staticmethod
    def _kind(d):
        info = d.get("info_dict") or {}
        vc, ac = info.get("vcodec", "none"), info.get("acodec", "none")
        if vc not in (None, "none") and ac in (None, "none"):
            return "video"           # video-only: an audio stream follows
        if ac not in (None, "none") and vc in (None, "none"):
            return "audio"
        return "file"

    def _stream_for(self, d):
        name = d.get("filename") or d.get("tmpfilename") or ""
        s = self._streams.get(name)
        if s is None:
            s = self._streams[name] = _Stream(self._kind(d))
        return s

    def _recompute(self):
        streams = list(self._streams.values())
        if not streams or all(s.frac is None for s in streams):
            return
        # video-only first => audio comes next and is a small slice of the work
        weights = (0.9, 0.1) if streams[0].kind == "video" else (1.0,)
        value = sum(w * (s.frac or 0.0) for w, s in zip(weights, streams))
        self._target = max(self._target or 0.0, min(1.0, value))   # never goes back

    def hook(self, d):
        """yt-dlp ``progress_hooks`` entry."""
        st = d.get("status")
        with self._lock:
            if self._closed:
                return
            if st == "downloading":
                s = self._stream_for(d)
                if self._phase in ("wait", "download"):
                    self._phase = "download"
                dl = d.get("downloaded_bytes") or 0
                tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                frac = None
                if tot > 0:
                    frac = dl / tot
                elif d.get("fragment_count"):
                    frac = max(0, (d.get("fragment_index") or 1) - 1) / d["fragment_count"]
                if frac is not None:
                    s.frac = max(s.frac or 0.0, min(1.0, frac))
            elif st == "finished":
                self._stream_for(d).frac = 1.0
            sp = d.get("speed")
            if st == "downloading" and sp:
                self._speed = sp if self._speed is None else 0.7 * self._speed + 0.3 * sp
                self._speed_at = self._clock()
            self._recompute()
            if self._target is not None:
                now = self._clock()
                self._samples.append((now, self._target))
                while self._samples and now - self._samples[0][0] > 10.0:
                    self._samples.popleft()
        self.start()
        if not self._tty():
            self._log_milestone()

    def postprocessor_hook(self, d):
        """yt-dlp ``postprocessor_hooks`` entry: merging / converting has begun."""
        if d.get("status") in ("started", "processing"):
            with self._lock:
                if not self._closed and self._phase != "done":
                    self._phase = "processing"
                    self._target = 1.0

    # ---------------------------------------------------------- speed / ETA
    @staticmethod
    def fmt_clock(sec):
        """3:40 or 1:02:10; "--:--" when unknown or absurd."""
        if sec is None or sec != sec or sec < 0 or sec > 100 * 3600:
            return "--:--"
        sec = int(round(sec))
        h, r = divmod(sec, 3600)
        m, ss = divmod(r, 60)
        return f"{h}:{m:02d}:{ss:02d}" if h else f"{m}:{ss:02d}"

    def _eta(self):
        """Seconds left for the WHOLE download (video + audio), from the pace of
        the last few seconds; None while there is not enough to go on."""
        now = self._clock()
        if self._target is None or len(self._samples) < 2:
            return None
        (t0, f0), (t1, f1) = self._samples[0], self._samples[-1]
        if t1 - t0 < 1.0 or now - t1 > 6.0:                 # too little data / stalled
            return None
        rate = (f1 - f0) / (t1 - t0)
        if rate <= 1e-6:
            return None
        return max(0.0, (1.0 - f1) / rate)

    def _details(self):
        """(speed text, eta text, elapsed text) for the current frame, or Nones."""
        if not self.details:
            return None, None, None
        speed = None
        if self._speed and self._clock() - self._speed_at < 4.0:
            speed = fmt_size(self._speed) + "/s"
        elapsed = None
        if self._started_at is not None:
            elapsed = self.fmt_clock(self._clock() - self._started_at)
        return speed, self.fmt_clock(self._eta()), elapsed

    # ------------------------------------------------------------ drawing
    def render_frame(self, cols=None, now=None):
        """The single line to show for terminal width `cols` (never wider than
        cols-1 cells). Pure apart from advancing the easing."""
        cols = cols or self._cols()
        now = time.monotonic() if now is None else now
        with self._lock:
            dt = max(0.0, min(0.25, now - self._last_t))
            self._last_t = now
            t = now - self._t0
            phase = self._phase
            if phase == "done":
                self._shown = 1.0
            elif self._target is not None:
                gap = self._target - self._shown
                self._shown += gap * min(1.0, 7.0 * dt) if abs(gap) > 0.0005 else gap
            shown = min(self._shown, 0.99) if phase != "done" else 1.0
            known = self._target is not None
            speed, eta, elapsed = self._details()
        return self._compose(cols, t, shown, known, phase, speed=speed, eta=eta, elapsed=elapsed)

    def _tail(self, phase, speed, eta, elapsed):
        """Candidate texts for the right-hand side, longest first."""
        if phase == "done":
            return [f"  in {elapsed}"] if elapsed else []
        if phase != "download" or not speed:
            return []
        return [f"  {speed:>10}  ETA {eta or '--:--':>5}", f"  {speed:>10}"]

    def _compose(self, cols, t, shown, known, phase, speed=None, eta=None, elapsed=None):
        avail = cols - 1                                # never touch the last column
        if avail < 2:
            return ""
        color = bool(theme._rgb(0, 0, 0))
        if phase == "done":
            label = "100%"
        elif known and phase != "processing":
            label = f"{int(shown * 100):>3}%"
        else:                                           # unknown size / merging: show it is alive
            label = f" {_SPIN[int(t * 12) % len(_SPIN)]} "
        tail = ""
        for cand in self._tail(phase, speed, eta, elapsed):
            if avail - (_LABEL_W + 1) - len(cand) >= 14:    # keep a decent bar
                tail = cand
                break
        bar_w = min(MAX_BAR, avail - _LABEL_W - 1 - len(tail))
        if bar_w < MIN_BAR:                             # tiny window: just the number
            return (label.strip() or "…")[:avail]
        bar = self._bar(bar_w, t, shown, known, phase, color)
        if color:
            lab = _paint((120, 232, 158) if phase == "done" else (245, 245, 245), bold=True)
            out = f" {bar} {lab}{label}\033[0m"
            if tail:
                if phase == "done":
                    out += _paint((120, 200, 150)) + tail + "\033[0m"
                elif "ETA" in tail:
                    sp, _, rest = tail.partition("  ETA")
                    out += _paint((235, 190, 90)) + sp + _paint((140, 140, 155)) + "  ETA" + rest + "\033[0m"
                else:
                    out += _paint((235, 190, 90)) + tail + "\033[0m"
            return out
        return f" {bar} {label}{tail}"

    def _palette(self, w, phase):
        if phase == "done":
            return _lerp_palette(_GREEN_A, _GREEN_B, w)
        stops = self.style["stops"]
        return _gradient(stops, w) if stops else theme.progress_palette(w)

    @staticmethod
    def _glow(anim, i, w, t, pos, sweep):
        """How much white to mix into filled cell `i` (0..1) for this motion."""
        if anim == "flicker":                                    # embers
            heat = 0.4 + 0.6 * (i / max(w - 1, 1))
            return 0.35 * sweep + 0.2 * (0.5 + 0.5 * math.sin(t * 11 + i * 1.9)) * heat
        if anim == "wave":
            return 0.45 * (0.5 + 0.5 * math.sin(t * 3.2 - i * 0.38)) + 0.4 * sweep
        if anim == "pulse":
            return 0.4 * (0.5 + 0.5 * math.sin(t * 4.0)) + 0.5 * sweep
        if anim == "rain":
            ph = (t * 1.1 + ((i * 0.6180339) % 1)) % 1
            return max(0.0, 1 - ph / 0.22) * 0.75 + 0.2 * sweep
        if anim == "flow":
            return 0.5 * sweep
        if anim == "none":
            return 0.0
        return sweep

    def _bar(self, w, t, shown, known, phase, color):
        if not color:                                   # NO_COLOR / dumb terminal
            if phase == "done" or (known and phase != "wait"):
                n = w if phase in ("done", "processing") else int(w * shown)
                return "#" * n + "-" * (w - n)
            c = int(_ease(abs(((t * 0.8) % 2) - 1)) * (w - 3))
            return "-" * c + "###" + "-" * (w - c - 3)
        if self.style["kind"] == "line":
            return self._bar_line(w, t, shown, known, phase)
        base = self._palette(w, phase)
        track = _paint(bg=_TRACK)
        out = []
        if not known and phase != "processing":
            # size unknown yet: a soft pulse bounces over the empty track
            p = (t * 0.7) % 2
            p = _ease(p if p <= 1 else 2 - p)
            centre = p * (w - 1)
            width = max(3.0, w / 6)
            for i in range(w):
                k = max(0.0, 1 - abs(i - centre) / width)
                out.append(_paint(bg=_mix(_TRACK, base[i], k)) + " ")
            return "".join(out) + "\033[0m"
        full_n = w if phase in ("done", "processing") else shown * w
        whole = int(full_n)
        edge = int((full_n - whole) * 8) if whole < w else 0
        # a light sweeps over the filled part; faster while finalising
        speed = 26 if phase == "processing" else 14
        span = max(whole, 1) + 8
        pos = (t * speed) % span - 4
        anim = self.style["anim"]
        flow = phase == "processing" or anim == "flow"
        for i in range(w):
            if i < whole:
                c = base[i]
                if flow:                                 # colours slide along the bar
                    c = base[int((i + t * (18 if phase == "processing" else 5)) % w)]
                sweep = max(0.0, 1 - abs(i - pos) / 3.5)
                k = self._glow("sweep" if phase in ("processing", "done") else anim, i, w, t, pos, sweep)
                if phase == "done":
                    k *= 0.8
                out.append(_paint(bg=_mix(c, _WHITE, 0.6 * min(1.0, k))) + " ")
            elif i == whole and edge:
                out.append(_paint(fg=base[i], bg=_TRACK) + _PARTIAL[edge])
            else:
                out.append(track + " ")
        return "".join(out) + "\033[0m"

    def _bar_line(self, w, t, shown, known, phase):
        """The "minimal" look: a thin line, ━━━━──────."""
        fill, dim = (205, 205, 215), (70, 70, 82)
        if phase == "done":
            fill = (110, 225, 150)
        if not known and phase != "processing":
            p = (t * 0.7) % 2
            centre = _ease(p if p <= 1 else 2 - p) * (w - 1)
            cells = []
            for i in range(w):
                k = max(0.0, 1 - abs(i - centre) / 3.0)
                cells.append(_paint(fg=_mix(dim, _WHITE, k)) + ("━" if k > 0.05 else "─"))
            return "".join(cells) + "\033[0m"
        full_n = w if phase in ("done", "processing") else shown * w
        whole = int(full_n)
        half = (full_n - whole) >= 0.5 and whole < w
        pos = (t * (24 if phase == "processing" else 12)) % (max(whole, 1) + 8) - 4
        cells = []
        for i in range(w):
            if i < whole:
                k = max(0.0, 1 - abs(i - pos) / 3.0)
                cells.append(_paint(fg=_mix(fill, _WHITE, 0.7 * k)) + "━")
            elif i == whole and half:
                cells.append(_paint(fg=fill) + "╸")
            else:
                cells.append(_paint(fg=dim) + "─")
        return "".join(cells) + "\033[0m"

    # ------------------------------------------------------- terminal ops
    def _write(self, text):
        try:
            self._out.write(text)
            self._out.flush()
        except (OSError, ValueError):
            pass

    def _draw(self):
        cols = self._cols()
        line = self.render_frame(cols)
        prefix = "\r"
        # window got narrower: the previous line has been re-wrapped by the
        # terminal onto several rows; step back up to its first row first
        if self._last_cols is not None and cols < self._last_cols and self._last_len >= cols:
            up = min(8, (self._last_len - 1) // cols)
            if up > 0:
                prefix = f"\033[{up}A\r\033[J"
        self._write(f"{prefix}{line}\033[K")
        self._last_cols, self._last_len = cols, visible_len(line)

    def _loop(self):
        period = 1.0 / FPS
        while not self._stop.wait(period):
            self._draw()

    def start(self):
        """Begin animating (idempotent). Safe to call before the first byte
        arrives: until the size is known the bar shows a bouncing pulse."""
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
        if not self._tty():
            return
        self._started_at = self._clock()
        self._write("\033[?25l")                        # hide the cursor
        self._draw()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _halt(self):
        self._stop.set()
        th = self._thread
        if th is not None and th is not threading.current_thread():
            th.join(timeout=1.0)
        self._thread = None

    def _log_milestone(self):
        pct = int((self._target or 0) * 100) // 10 * 10
        if pct > self._last_milestone and pct < 100:
            self._last_milestone = pct
            self._write(f"  {pct}%\n")

    def finish(self):
        """Download succeeded: show the finished green bar and move to the next line."""
        with self._lock:
            if self._closed:
                return
            self._phase, self._target, self._shown = "done", 1.0, 1.0
            self._closed = True
        self._halt()
        if self._tty():
            if self._started:
                self._draw()
                self._write("\033[?25h\n")
        elif self._started:
            self._write("  100%\n")

    def close(self):
        """Download failed or was cancelled: remove the bar without a trace."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._halt()
        if self._tty() and self._started:
            up = 0
            if self._last_cols and self._last_len > self._cols():
                up = min(8, (self._last_len - 1) // self._cols())
            self._write((f"\033[{up}A" if up else "") + "\r\033[J\033[?25h")


def preview_all(current=None, stream=None, seconds=3.2, fps=20, cols=None):
    """Show every style at once, all animating, so the user can pick by eye.
    On a terminal the bars fill up and glow for a few seconds, then freeze at
    62 %; when piped it prints one plain frame per style."""
    out = stream or sys.stdout
    cols = cols or max(20, shutil.get_terminal_size((80, 24)).columns)
    try:
        tty = out.isatty()
    except (AttributeError, ValueError):
        tty = False
    rends = [(name, ProgressRenderer(style=name)) for name in STYLE_ORDER]
    label_w = max(len(STYLES[n]["label"]) for n in STYLE_ORDER)
    inner = max(8, cols - (label_w + 9))

    def frame(t, shown):
        lines = []
        for i, (name, r) in enumerate(rends, 1):
            mark = "●" if name == current else " "
            head = f"  {i}. {mark} {STYLES[name]['label']:<{label_w}}"
            lines.append(head + r._compose(inner, t, shown, True, "download",
                                           speed="7.4 MB/s", eta=r.fmt_clock(max(0.0, (1 - shown) * 40))))
        return lines

    if not tty:
        for ln in frame(1.0, 0.62):
            out.write(_ANSI.sub("", ln) + "\n")
        out.flush()
        return
    n = len(rends)
    frames = int(seconds * fps)
    try:
        out.write("\033[?25l")
        for f in range(frames + 1):
            t = f / fps
            shown = 0.62 if f == frames else min(0.62, (t / (seconds * 0.7)) * 0.62)
            if f:
                out.write(f"\033[{n}A")
            out.write("\n".join(l + "\033[K" for l in frame(t, shown)) + "\n")
            out.flush()
            if f < frames:
                time.sleep(1 / fps)
    except KeyboardInterrupt:
        pass
    finally:
        out.write("\033[?25h")
        out.flush()
