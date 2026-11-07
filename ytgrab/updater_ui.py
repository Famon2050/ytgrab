"""The live display of `ytgrab self-update`.

While pip / pipx installs the new version, the installer's own chatter is captured (it used
to be printed over the animation and garble it), mapped to real stages (resolving, downloading,
building, installing...) and shown as one stable two-line panel:

    ⠹  Installing            ▕██████████░░░░░░░░░░▏  52%   0:07
       Building wheel for ytgrab (pyproject.toml)

Everything the installer printed is kept, and shown only if something goes wrong.
Piped / non-interactive runs skip all of this and let the installer print normally.
"""

import atexit
import collections
import os
import re
import signal
import subprocess
import sys
import threading
import time

from . import theme
from .progress import ProgressRenderer
from .theme import C, paint

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# (pattern, progress the bar may reach once seen, label)
STAGES = (
    (re.compile(r"^(Processing |Looking in |Obtaining |Running command)"), 0.06, "Preparing"),
    (re.compile(r"^(Collecting|Requirement already satisfied|Using cached|Resolving)"), 0.18, "Resolving packages"),
    (re.compile(r"^Downloading|^\s*Downloading", re.I), 0.34, "Downloading"),
    (re.compile(r"(Installing build dependencies|Getting requirements to build|Preparing (editable )?metadata"
                r"|Building (editable |wheel)|Created wheel|Building)", re.I), 0.52, "Building"),
    (re.compile(r"^(Attempting uninstall|Found existing installation|Uninstalling|Successfully uninstalled)"),
     0.66, "Replacing the old version"),
    (re.compile(r"^Installing collected packages"), 0.78, "Installing"),
    (re.compile(r"^(Successfully installed|installed package|These apps are now globally available|done!)", re.I),
     0.94, "Finishing"),
)
PIP_ENV = {"PIP_PROGRESS_BAR": "off", "PIP_NO_COLOR": "1", "NO_COLOR": "1",
           "PYTHONUNBUFFERED": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}


def clean_line(text):
    return _ANSI.sub("", text).replace("\t", " ").strip()


def clock(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def clip(text, width):
    """Cut plain text to `width` cells, adding an ellipsis."""
    if len(text) <= width:
        return text
    return text[:max(width, 0)] if width <= 1 else text[:width - 1] + "…"


class UpdateView:
    """Two-line live panel. `render()` is pure so it can be tested without a terminal."""

    def __init__(self, title="Installing", style="classic", out=None, clock_fn=time.monotonic):
        self.title, self.out = title, out
        self._bar = ProgressRenderer(style=style)
        self._clock = clock_fn
        self._t0 = clock_fn()
        self._stage_t = self._t0
        self.floor, self.label = 0.02, "Starting"
        self.last = ""
        self.note = ""
        self.shown = 0.0
        self.done = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._hidden = False
        self._last_draw = self._t0

    # ------------------------------------------------------------ state
    def feed(self, line):
        line = clean_line(line)
        if not line:
            return
        with self._lock:
            self.last = line
            for pat, floor, label in STAGES:
                if pat.search(line) and floor > self.floor:
                    self.floor, self.label, self._stage_t = floor, label, self._clock()
                    break

    def set_note(self, text):
        with self._lock:
            self.note = text

    def _target(self, now):
        # between real stage changes the bar creeps forward a little, so it never looks frozen,
        # but it can never run past what the installer has actually reported
        creep = min(0.10, 0.012 * max(0.0, now - self._stage_t))
        return 0.98 if self.done else min(0.97, self.floor + creep)

    # ----------------------------------------------------------- drawing
    def render(self, cols, now=None, advance=True):
        """The two panel lines for a terminal `cols` wide (each at most cols-1 cells)."""
        now = self._clock() if now is None else now
        avail = max(1, cols - 1)
        with self._lock:
            if advance:
                dt = max(0.0, min(0.3, now - self._last_draw))
                self._last_draw = now
                gap = self._target(now) - self.shown
                self.shown += gap * min(1.0, 6.0 * dt) if abs(gap) > 0.002 else gap
            frac = max(0.0, min(1.0, self.shown))
            label, last, note = self.label, self.last, self.note
        t = now - self._t0
        spin = "|/-\\"[int(t * 8) % 4] if os.environ.get("YTGRAB_ASCII") else _SPIN[int(t * 12) % len(_SPIN)]
        pct = f"{int(frac * 100):>3}%"
        elapsed = clock(t)
        title = f"{self.title}"
        head = f"  {spin}  {title}"
        # width budget: head + gap + bar + pct + elapsed
        room = avail - len(head) - len(pct) - len(elapsed) - 7
        bar_w = max(0, min(30, room))
        if avail < 24:                                          # a very narrow window: just spinner + percent
            row = paint(f" {spin} {pct.strip()}"[:avail], C.RED_HOT, C.BOLD)
            return [row, paint(clip("  " + (note or last or label), avail), C.GREY)]
        if bar_w >= 8:
            bar = "▕" + self._bar._bar(bar_w, t, frac, True, "download", bool(theme._COLOR)) + "▏"
            row = (paint(f"  {spin}  ", C.RED_HOT, C.BOLD) + paint(title, C.WHITE, C.BOLD) + "  " + bar + " "
                   + paint(pct, C.WHITE, C.BOLD) + "  " + paint(elapsed, C.GREY_D))
        else:
            row = (paint(f"  {spin}  ", C.RED_HOT, C.BOLD) + paint(clip(title, max(4, avail - 14)), C.WHITE, C.BOLD)
                   + " " + paint(pct.strip(), C.WHITE, C.BOLD))
        detail = note or last or label
        second = paint("     " + clip(f"{label}: {detail}" if (last and not note) and label else detail,
                                      max(4, avail - 6)), C.AMBER if note else C.GREY)
        return [row, second]

    # ----------------------------------------------------------- terminal
    def _write(self, text):
        out = self.out or sys.stdout
        try:
            out.write(text)
            out.flush()
        except (OSError, ValueError):
            pass

    def _draw(self, first=False):
        import shutil
        cols = shutil.get_terminal_size((80, 24)).columns
        try:
            rows = self.render(cols)
        except Exception:                       # cosmetic only: never let drawing hurt the install
            return
        up = "" if first else "\033[2A"
        self._write(f"{up}\r{rows[0]}\033[K\n\r{rows[1]}\033[K\n")

    def start(self):
        if self._thread is not None:
            return
        self._write("\033[?25l")
        self._hidden = True
        atexit.register(self._show_cursor)
        self._draw(first=True)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(0.06):
            self._draw()

    def _show_cursor(self):
        if self._hidden:
            self._write("\033[?25h")
            self._hidden = False

    def stop(self, ok=True):
        """Stop animating, wipe the panel and restore the cursor."""
        with self._lock:
            self.done = ok
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
            self._write("\033[2A\r\033[J")
        self._show_cursor()


class InstallResult:
    def __init__(self, rc, tail, seconds, aborted=False):
        self.rc, self.tail, self.seconds, self.aborted = rc, tail, seconds, aborted

    @property
    def ok(self):
        return self.rc == 0 and not self.aborted


def _iter_lines(stream):
    """Yield text segments from a byte stream, splitting on \\n AND \\r (pip redraws with \\r)."""
    read = getattr(stream, "read1", None) or stream.read
    buf = ""
    while True:
        try:
            chunk = read(4096)
        except (OSError, ValueError):
            break
        if not chunk:
            break
        buf += chunk.decode("utf-8", "replace")
        parts = re.split(r"[\r\n]+", buf)
        buf = parts.pop()
        for p in parts:
            if p.strip():
                yield p
    if buf.strip():
        yield buf


def run_installer(cmd, title="Installing ytgrab", style="classic", interactive=None,
                  popen=subprocess.Popen, out=None):
    """Run an install command. Interactive terminals get the live panel (and the installer's
    output is captured); anything else gets the installer's normal output, untouched."""
    t0 = time.monotonic()
    if interactive is None:
        interactive = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if not interactive:
        rc = popen(cmd).wait()
        return InstallResult(rc, [], time.monotonic() - t0)

    env = dict(os.environ)
    env.update(PIP_ENV)
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    view = UpdateView(title, style=style, out=out)
    tail = collections.deque(maxlen=80)

    def pump():
        for line in _iter_lines(proc.stdout):
            tail.append(clean_line(line))
            view.feed(line)

    state = {"last": 0.0, "abort": False}

    def on_sigint(signum, frame):
        now = time.monotonic()
        if now - state["last"] < 3.0:
            state["abort"] = True
        else:
            view.set_note("Finishing safely, press Ctrl+C again within 3 s to abort and roll back")
        state["last"] = now

    old = None
    try:
        old = signal.signal(signal.SIGINT, on_sigint)       # an interrupted install can leave a broken tool
    except (ValueError, OSError):                           # not the main thread
        old = None
    view.start()
    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    aborted = False
    try:
        while proc.poll() is None:
            if state["abort"] and not aborted:
                aborted = True
                view.set_note("Aborting, then restoring the previous version…")
                try:
                    proc.terminate()
                except OSError:
                    pass
            time.sleep(0.04)
        reader.join(timeout=2.0)
    finally:
        if old is not None:
            try:
                signal.signal(signal.SIGINT, old)
            except (ValueError, OSError):
                pass
        rc = proc.poll()
        view.stop(ok=(rc == 0 and not aborted))
    return InstallResult(rc if rc is not None else 1, list(tail), time.monotonic() - t0, aborted)


# ----------------------------------------------------------------------
# Demo: the real panel against a scripted fake installer (installs nothing)
# ----------------------------------------------------------------------
DEMO_SCRIPT = (
    "Processing /home/you/ytgrab", "Collecting yt-dlp>=2024", "  Downloading yt_dlp-2026.1.1-py3-none-any.whl",
    "Collecting textual>=0.60", "  Downloading textual-0.80.0-py3-none-any.whl",
    "  Installing build dependencies ... done", "  Building wheel for ytgrab (pyproject.toml) ... done",
    "Attempting uninstall: ytgrab", "  Successfully uninstalled ytgrab-1.10.0",
    "Installing collected packages: yt-dlp, textual, ytgrab", "Successfully installed ytgrab-1.11.0",
)


class FakeInstaller:
    """Looks like a Popen whose output is a script. Used only by the demo and by tests."""

    def __init__(self, script=DEMO_SCRIPT, delay=0.3, returncode=0):
        r, w = os.pipe()
        self.stdout = os.fdopen(r, "rb", buffering=0)
        self.returncode = None
        self._final = returncode
        self._script, self._delay = tuple(script), delay
        threading.Thread(target=self._run, args=(w,), daemon=True).start()

    def _run(self, w):
        try:
            for line in self._script:
                if self.returncode is not None:
                    break
                os.write(w, (line + "\n").encode())
                time.sleep(self._delay)
        finally:
            os.close(w)
            if self.returncode is None:
                self.returncode = self._final

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        while self.returncode is None:
            time.sleep(0.01)
        return self.returncode

    def terminate(self):
        self.returncode = -15

    kill = terminate
