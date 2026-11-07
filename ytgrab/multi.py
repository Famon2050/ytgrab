"""Concurrent multi-download with a live table and pause/resume/cancel."""

import os
import re
import shutil
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import yt_dlp

from .fmt import fmt_duration, fmt_size, short_error
from .theme import C, paint


MAX_CONCURRENT_HARD_CAP = 4
_TITLE_W = 34          # widest title column
_BAR_W = 16
_FINAL = ("done", "error", "cancelled")


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _clip(line, width):
    """Cut a line that contains colour codes to `width` visible cells."""
    out, used, i = "", 0, 0
    while i < len(line):
        m = _ANSI_RE.match(line, i)
        if m:
            out += m.group(0); i = m.end(); continue
        w = _cells(line[i])
        if used + w > width:
            break
        out += line[i]; used += w; i += 1
    return out + "\033[0m"


def _cells(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _fit(text, width):
    """Truncate/pad to `width` terminal cells (CJK and emoji count double)."""
    text = str(text)
    if sum(_cells(c) for c in text) <= width:
        return text + " " * (width - sum(_cells(c) for c in text))
    out, used = "", 0
    for ch in text:
        if used + _cells(ch) > width - 1:
            break
        out += ch
        used += _cells(ch)
    return out + "…" + " " * (width - used - 1)


class _Cancelled(Exception):
    pass


@dataclass
class Job:
    idx: int
    url: str
    title: str
    kind: str
    quality: str
    opts: dict
    status: str = "queued"
    pct: int = 0
    speed: str = ""
    eta: str = ""
    error: str = ""
    size: int = 0
    filepath: str = ""
    channel: str = ""
    archive_key: str = ""
    _pause: threading.Event = field(default_factory=threading.Event)
    _cancel: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self):
        self._pause.set()  # set = running, clear = paused

    def reset(self):
        """Make a failed job runnable again (retry)."""
        self.status = "queued"; self.pct = 0; self.speed = ""; self.eta = ""
        self.error = ""; self.size = 0; self.filepath = ""
        self._pause.set(); self._cancel.clear()


class _NullLogger:
    """Swallow yt-dlp's own console output (the TUI owns the screen)."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


class JobRunner:
    """Runs Jobs on a thread pool with pause / resume / cancel. UI-agnostic:
    subclasses override _changed(); on_finish(job) fires in the worker thread."""

    def __init__(self, workers=3, on_finish=None, quiet=False):
        self.workers = max(1, min(int(workers), MAX_CONCURRENT_HARD_CAP))
        self.on_finish = on_finish
        self.quiet = quiet
        self._executor = None

    def _changed(self):
        pass

    def submit(self, job):
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self.workers)
        return self._executor.submit(self._run_one, job)

    def shutdown(self, jobs=()):
        """Cancel everything and release the pool without waiting."""
        for j in jobs:
            j._cancel.set(); j._pause.set()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    # ---------------- yt-dlp hook ----------------
    def _hook_for(self, job):
        def hook(d):
            if job._cancel.is_set():
                raise _Cancelled()
            while not job._pause.is_set():
                if job._cancel.is_set():
                    raise _Cancelled()
                time.sleep(0.1)

            st = d.get("status")
            if st == "downloading":
                dl = d.get("downloaded_bytes") or 0
                tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                job.pct = int(dl * 100 / tot) if tot > 0 else 0
                sp, eta = d.get("speed"), d.get("eta")
                job.speed = f"{fmt_size(sp)}/s" if sp else ""
                job.eta = fmt_duration(eta) if eta else ""
                if job.status != "paused":
                    job.status = "downloading"
                self._changed()
            elif st == "finished":
                job.pct = 100
                job.status = "processing"
                self._changed()
        return hook

    @staticmethod
    def _result_of(info):
        """(path, bytes on disk) of a finished download; ("", 0) if unknown."""
        info = info or {}
        reqs = info.get("requested_downloads") or []
        fp = info.get("filepath") or info.get("_filename") or ""
        if not fp:
            fp = next((r.get("filepath") for r in reqs if r.get("filepath")), "")
        try:
            return fp, (os.path.getsize(fp) if fp else 0)
        except OSError:
            return fp, 0

    def _run_one(self, job):
        opts = dict(job.opts)
        opts["progress_hooks"] = [self._hook_for(job)]
        if self.quiet:
            opts["logger"] = _NullLogger()
        try:
            while not job._pause.is_set() and not job._cancel.is_set():
                time.sleep(0.1)
            if job._cancel.is_set():
                job.status = "cancelled"
                return job
            if job.status == "paused":
                job.status = "queued"

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(job.url, download=True)
            job.filepath, job.size = self._result_of(info)
            job.status = "done"
            job.pct = 100
        except _Cancelled:
            job.status = "cancelled"
            job.speed = ""
            job.eta = ""
        except Exception as e:
            if job._cancel.is_set():
                job.status = "cancelled"
            else:
                job.status = "error"
                job.error = short_error(e)
        finally:
            self._changed()
            if self.on_finish:
                try:
                    self.on_finish(job)
                except Exception:
                    pass
        return job



class MultiDownloader(JobRunner):
    def __init__(self, jobs, workers=3, on_finish=None):
        self.jobs = jobs
        for n, j in enumerate(self.jobs, 1):   # display/selection number = position
            j.idx = n
        self._user_finish = on_finish          # runs in the worker thread, right when a job ends
        super().__init__(workers, on_finish=self._finished)
        self.workers = max(1, min(self.workers, len(jobs) or 1))
        self._tty = sys.stdout.isatty()        # no cursor tricks when piped (cron logs)
        self._render_lock = threading.Lock()
        self._lines_drawn = 0
        self._focus = 0
        self._key_stop = threading.Event()

    # ---------------- keyboard ----------------
    def _keyboard_loop(self):
        if os.name == "nt":
            try:
                import msvcrt
            except ImportError:
                return
            if not sys.stdin.isatty():
                return
            while not self._key_stop.is_set():
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch not in ("\x00", "\xe0"):
                        self._handle_key(ch)
                else:
                    self._key_stop.wait(0.05)
            return
        try:
            import termios, tty, select
        except ImportError:
            return
        if not sys.stdin.isatty():
            return
        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
        except Exception:
            return
        try:
            tty.setcbreak(fd)
            while not self._key_stop.is_set():
                if not select.select([sys.stdin], [], [], 0.15)[0]:
                    continue
                try:
                    ch = os.read(fd, 1).decode("utf-8", "ignore")
                except Exception:
                    continue
                if not ch:
                    continue
                self._handle_key(ch)
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass

    def _handle_key(self, ch):
        if ch.isdigit() and ch != "0":
            n = int(ch)
            if 1 <= n <= len(self.jobs):
                self._focus = n
                self._render()
            return
        if ch in ("0", "\x1b"):
            self._focus = 0
            self._render()
            return
        # j / k move the selection, so jobs past #9 can be picked too
        if ch in ("j", "k"):
            step = 1 if ch == "j" else -1
            self._focus = (self._focus + step) % (len(self.jobs) + 1)
            self._render()
            return
        key = ch.lower()
        if key not in ("p", "r", "c"):
            return
        targets = [self.jobs[self._focus - 1]] if self._focus else self.jobs

        if key == "p":
            for j in targets:
                if j.status in ("queued", "downloading"):
                    j._pause.clear()
                    j.status = "paused"
        elif key == "r":
            for j in targets:
                if j.status == "paused":
                    j._pause.set()
                    j.status = "downloading" if j.pct > 0 else "queued"
        elif key == "c":
            for j in targets:
                if j.status not in ("done", "error", "cancelled"):
                    j._cancel.set()
                    j._pause.set()
        self._render()

    def _changed(self):
        self._render()

    def _finished(self, job):
        if not self._tty:                      # a plain log line instead of the live table
            mark = {"done": "✓", "error": "✗", "cancelled": "‖"}.get(job.status, "?")
            print(f"  {mark} {job.title[:60]}" + (f"  —  {job.error}" if job.error else ""), flush=True)
        if self._user_finish:
            self._user_finish(job)

    # ---------------- table ----------------
    def _bar(self, pct, done=False, err=False, paused=False, w=_BAR_W):
        filled = w if done else int(w * pct / 100)
        if err:
            return paint("░" * w, C.GREY_D)
        if done:
            return paint("█" * w, C.GREEN)
        if paused:
            return (paint("▒" * filled, C.AMBER)
                    + paint("░" * (w - filled), C.GREY_D))
        return (paint("█" * filled, C.RED)
                + paint("░" * (w - filled), C.GREY_D))

    def _right(self, j, compact=False):
        if compact:                                    # tiny window: 4 cells
            sym = {"queued": "…", "paused": "‖", "processing": "…",
                   "done": "✓", "cancelled": "✗", "error": "✗"}
            if j.status == "downloading":
                return paint(f"{j.pct:>3}%", C.WHITE, C.BOLD)
            color = {"done": C.GREEN, "error": C.RED}.get(j.status, C.AMBER)
            return paint(sym.get(j.status, "?").ljust(4), color, C.BOLD)
        if j.status == "queued":
            return paint("waiting".ljust(20), C.GREY_D)
        if j.status == "downloading":
            sp = j.speed or "--"
            eta = f"ETA {j.eta}" if j.eta else ""
            return (paint(f"{j.pct:>3}%", C.WHITE, C.BOLD)
                    + "  " + paint(f"{sp:>9}", C.AMBER)
                    + ("  " + paint(eta, C.GREY_D) if eta else ""))
        if j.status == "paused":
            return paint("‖ paused".ljust(20), C.AMBER, C.BOLD)
        if j.status == "processing":
            return paint("processing…".ljust(20), C.GREEN)
        if j.status == "done":
            return paint("✓ done".ljust(20), C.GREEN, C.BOLD)
        if j.status == "cancelled":
            return paint("✗ cancelled".ljust(20), C.AMBER, C.BOLD)
        return paint("✗ error".ljust(20), C.RED, C.BOLD)

    @staticmethod
    def _widths(cols):
        """(title, bar, right) cell widths so a row fits in cols-1 cells."""
        avail = cols - 1
        tw, bw, rw = _TITLE_W, _BAR_W, 20
        over = lambda: 11 + tw + bw + rw - avail
        tw = max(8, tw - max(0, over()))
        bw = max(4, bw - max(0, over()))
        if over() > 0:
            rw = 4                                     # compact status
        if over() > 0:
            tw = max(0, tw - over())
        if over() > 0:
            bw = max(0, bw - over())
        return tw, bw, rw

    def _row(self, j):
        marker = "▶" if self._focus == j.idx else " "
        num = paint(f"{marker}{j.idx:>2}.", C.RED, C.BOLD)
        cols = shutil.get_terminal_size((80, 24)).columns
        tw, bw, rw = self._widths(cols)
        bar = self._bar(j.pct,
                        done=(j.status == "done"),
                        err=(j.status == "error"),
                        paused=(j.status == "paused"), w=bw)
        parts = [" " + num]
        if tw:
            label = j.title
            if getattr(j, "kind", None) == "video":
                quality = f"{j.quality}p" if str(j.quality).isdigit() else str(j.quality)
                container = str(j.opts.get("merge_output_format", "mp4")).upper()
                label = f"{quality} {container}  ·  {label}"
            parts.append(paint(_fit(label, tw), C.OFF))
        if bw:
            parts.append(bar)
        parts.append(self._right(j, compact=(rw == 4)))
        return _clip("  ".join(parts), cols - 1)           # a row must never wrap

    def _window(self):
        """Rows to draw. With more jobs than terminal lines, scroll: finished jobs
        drop off the top, and a footer says what is hidden."""
        cap = max(4, shutil.get_terminal_size((80, 24)).lines - 8)
        if len(self.jobs) <= cap:
            return self.jobs, None
        room = cap - 1                                    # one line for the footer
        first = next((i for i, j in enumerate(self.jobs) if j.status not in _FINAL), 0)
        start = min(first, len(self.jobs) - room)
        if self._focus and not start < self._focus <= start + room:
            start = max(0, min(self._focus - 1, len(self.jobs) - room))
        shown = self.jobs[start:start + room]
        hidden_done = sum(1 for j in self.jobs if j.status == "done")
        hidden_err = sum(1 for j in self.jobs if j.status == "error")
        foot = (f"     … showing {start + 1}-{start + len(shown)} of {len(self.jobs)}"
                f"   ✓ {hidden_done} done" + (f"   ✗ {hidden_err} failed" if hidden_err else ""))
        return shown, paint(foot, C.GREY_D)

    def _render(self):
        if not self._tty:
            return
        with self._render_lock:
            shown, foot = self._window()
            cols = shutil.get_terminal_size((80, 24)).columns
            lines = [self._row(j) for j in shown] + ([_clip(foot, cols - 1)] if foot else [])
            buf = ""
            if self._lines_drawn:
                buf += f"\033[{self._lines_drawn}A\033[J"
            for r in lines:
                buf += r + "\n"
            sys.stdout.write(buf)
            sys.stdout.flush()
            self._lines_drawn = len(lines)

    # ---------------- public ----------------
    def run(self):
        print()
        print(paint(f"  ⬇  {len(self.jobs)} item(s)  ·  up to {self.workers} at once",
                    C.WHITE, C.BOLD))
        print(paint("  keys:  1-9 / j k select   p pause   r resume   c cancel   0 clear",
                    C.GREY_D))
        print()
        self._render()

        kb = threading.Thread(target=self._keyboard_loop, daemon=True)
        kb.start()
        ex = ThreadPoolExecutor(max_workers=self.workers)
        futures = [ex.submit(self._run_one, j) for j in self.jobs]
        interrupted = False
        try:
            try:
                for _ in as_completed(futures):
                    pass
            except KeyboardInterrupt:
                # Stop waiting for in-flight network/post-processing calls. Their
                # progress hooks observe cancellation when control returns.
                interrupted = True
                for j in self.jobs:
                    j._cancel.set(); j._pause.set()
                for f in futures:
                    f.cancel()
        finally:
            ex.shutdown(wait=not interrupted, cancel_futures=interrupted)

            self._key_stop.set()
            kb.join(timeout=0.5)

        for j in self.jobs:
            if j.status in ("queued", "paused", "downloading", "processing") \
                    and j._cancel.is_set():
                j.status = "cancelled"
        self._render()
        print()
        done = [j for j in self.jobs if j.status == "done"]
        errs = [j for j in self.jobs if j.status == "error"]
        cancelled = [j for j in self.jobs if j.status == "cancelled"]

        print(paint(f"  ✓  {len(done)}/{len(self.jobs)} succeeded",
                    C.GREEN, C.BOLD))
        if cancelled:
            print(paint(f"  ‖  {len(cancelled)} cancelled", C.AMBER))
        if errs:
            print()
            print(paint("  Failed:", C.RED, C.BOLD))
            for j in errs:
                print(paint(f"    · {j.title[:50]}  —  {j.error}", C.GREY))
        return done, errs
