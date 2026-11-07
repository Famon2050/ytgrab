"""Stream YouTube in VLC instead of downloading it: the engine behind `ytstream`.

* ``resolve``      asks yt-dlp for the direct stream URLs (nothing is downloaded);
* ``vlc_command``  builds the VLC command line (video + separate audio, start time, remote control);
* ``RcClient``     talks to VLC's built-in remote-control interface to read the playback position;
* ``PlaySession``  runs VLC and keeps the position / length up to date until it is closed;
* the watch history (``record``, ``resume_point``...) that remembers where you stopped.
"""

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from . import ytdl
from .config import CONFIG_DIR, atomic_write_text, file_lock, read_json

STREAM_FILE = CONFIG_DIR / "stream_history.json"
QUALITIES = ("best", "1080", "720", "480", "360")
HISTORY_LIMIT = 300
MIN_RESUME = 15            # seconds: before this a video is simply "started"
FINISHED_RATIO = 0.95      # at or beyond this share of the video it counts as watched
_MAC_VLC = "/Applications/VLC.app/Contents/MacOS/VLC"


class StreamError(Exception):
    pass


class _Silent:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


# ======================================================================
# watch history
# ======================================================================
def load_history():
    return [h for h in read_json(STREAM_FILE, [], list) if isinstance(h, dict)]


def _save(hist):
    atomic_write_text(STREAM_FILE, json.dumps(hist[:HISTORY_LIMIT], indent=2, ensure_ascii=False))


def _same(h, key):
    return bool(key) and key in (h.get("id"), h.get("url"))


def get(key):
    return next((h for h in load_history() if _same(h, key)), None)


def record(info, position=None, duration=None, finished=None, new_session=False):
    """Create or update the history entry for a video and move it to the top.
    `info` carries id/url/title/channel; position/duration are seconds."""
    with file_lock(STREAM_FILE):
        hist = load_history()
        key = info.get("id") or info.get("url")
        old = next((h for h in hist if _same(h, key) or _same(h, info.get("url"))), None)
        entry = dict(old or {})
        for k in ("id", "url", "title", "channel"):
            if info.get(k):
                entry[k] = info[k]
        if position is not None:
            entry["position"] = max(0, int(position))
        if duration:
            entry["duration"] = int(duration)
        if finished is not None:
            entry["finished"] = bool(finished)
        entry["watched_at"] = time.strftime("%Y-%m-%d %H:%M")
        entry["count"] = int(entry.get("count", 0)) + (1 if new_session or old is None else 0)
        hist = [entry] + [h for h in hist if h is not old]
        _save(hist)
        return entry


def remove(key):
    with file_lock(STREAM_FILE):
        hist = load_history()
        keep = [h for h in hist if not _same(h, key)]
        _save(keep)
        return len(keep) != len(hist)


def clear():
    with file_lock(STREAM_FILE):
        _save([])


def resume_point(entry):
    """Seconds to start from for a remembered video (0 = from the beginning)."""
    if not entry or entry.get("finished"):
        return 0
    pos, dur = int(entry.get("position") or 0), int(entry.get("duration") or 0)
    if pos < MIN_RESUME or (dur and pos >= dur * FINISHED_RATIO):
        return 0
    return pos


def continue_list(hist=None):
    """Videos you can pick up again, most recently watched first."""
    return [h for h in (load_history() if hist is None else hist) if resume_point(h)]


def is_finished(position, duration, ended_cleanly=False):
    if not duration:
        return False
    return position >= duration * FINISHED_RATIO or (ended_cleanly and position >= duration - 20)


def clock(sec):
    sec = max(0, int(sec or 0))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ======================================================================
# resolving the stream
# ======================================================================
def stream_format(quality):
    cap = quality if quality in QUALITIES and quality != "best" else None
    if cap is None:
        return "bestvideo*+bestaudio/best/best"

    # yt-dlp's height filter incorrectly treats the long edge of portrait
    # Shorts as their resolution. Select formats using the same short-edge
    # rule as downloads, and never fall back to an uncapped format.
    def select_capped(ctx):
        formats = ctx.get("formats", [])

        def short_edge(fmt):
            return ytdl._res(fmt)

        def score(fmt):
            return (short_edge(fmt), fmt.get("fps") or 0, fmt.get("tbr") or 0,
                    str(fmt.get("vcodec") or "").startswith("avc1"),
                    str(fmt.get("acodec") or "").startswith("mp4a"))

        videos = [f for f in formats
                  if f.get("vcodec") != "none" and short_edge(f) and short_edge(f) <= int(cap)]
        if not videos:
            return
        videos.sort(key=score)
        video = videos[-1]

        audios = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") != "none"]
        if audios:
            audio = max(audios, key=lambda f: (f.get("abr") or f.get("tbr") or 0,
                                               str(f.get("acodec") or "").startswith("mp4a")))
            yield {
                "requested_formats": [video, audio],
                "format_id": f"{video.get('format_id', '')}+{audio.get('format_id', '')}",
                "format": f"{video.get('format', video.get('format_id', ''))}+{audio.get('format', audio.get('format_id', ''))}",
                "ext": video.get("ext"),
                "width": video.get("width"),
                "height": video.get("height"),
                "vcodec": video.get("vcodec"),
                "acodec": audio.get("acodec"),
                "protocol": f"{video.get('protocol', '')}+{audio.get('protocol', '')}",
            }
            return

        # Only a combined stream is available; use the highest eligible one.
        combined = [f for f in videos if f.get("acodec") != "none"]
        if combined:
            yield combined[-1]

    return select_capped


def stream_opts(cfg, quality=None):
    q = quality or cfg.get("stream_quality", "1080")
    q = q if q in QUALITIES else "1080"
    sort = ([f"res:{q}"] if q != "best" else []) + ["vcodec:h264", "acodec:aac"]
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
            "logger": _Silent(), "format": stream_format(q), "format_sort": sort}
    ytdl._apply_auth(opts, cfg)
    ytdl._apply_common(opts, cfg)
    opts.pop("ratelimit", None)                     # a download speed limit makes no sense for playback
    return opts


def streams_from_info(info):
    """The URLs VLC needs from a yt-dlp info dict: one combined stream, or video + audio."""
    formats = info.get("requested_formats") or []
    video = audio = None
    for f in formats:
        vc, ac = f.get("vcodec") or "none", f.get("acodec") or "none"
        if vc != "none" and video is None:
            video = f
        elif ac != "none" and vc == "none" and audio is None:
            audio = f
    if video is None:
        video = formats[0] if formats else info
        audio = None
    if not video.get("url"):
        raise StreamError("yt-dlp found no playable stream for this video.")
    headers = video.get("http_headers") or info.get("http_headers") or {}
    return {
        "id": info.get("id"), "url": info.get("webpage_url") or info.get("original_url"),
        "title": info.get("title") or "YouTube video", "channel": info.get("uploader") or info.get("channel") or "",
        "duration": int(info.get("duration") or 0), "is_live": bool(info.get("is_live")),
        "video_url": video["url"], "audio_url": audio["url"] if audio else None,
        "user_agent": headers.get("User-Agent", ""), "height": video.get("height") or info.get("height"),
        "ext": info.get("ext"),
    }


def resolve(url, cfg, quality=None, extractor=None):
    """Find the direct stream for a YouTube link. Raises StreamError with a readable message."""
    opts = stream_opts(cfg, quality)
    info, err = (extractor or ytdl._extract_with_auth)(url, opts, False)
    if err or not info:
        raise StreamError(ytdl.short_error(err) if err and hasattr(ytdl, "short_error") else str(err or "no info"))
    if info.get("entries") is not None:              # a playlist link: play its first video
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise StreamError("That playlist is empty.")
        info = entries[0]
    out = streams_from_info(info)
    out["url"] = out["url"] or url
    return out


# ======================================================================
# VLC
# ======================================================================
def find_vlc(cfg=None):
    """Path of the VLC program, or None. The `stream_player` setting wins."""
    custom = (cfg or {}).get("stream_player")
    if custom:
        return custom if (Path(custom).exists() or shutil.which(custom)) else None
    found = shutil.which("vlc") or shutil.which("vlc.exe")
    if found:
        return found
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            candidate = Path(base) / "VideoLAN" / "VLC" / "vlc.exe" if base else None
            if candidate and candidate.is_file():
                return str(candidate)
    if Path(_MAC_VLC).exists():
        return _MAC_VLC
    return None


def install_hint():
    import sys
    if sys.platform == "darwin":
        return "brew install --cask vlc      (or download it from videolan.org)"
    if os.name == "nt":
        return "Install VLC from videolan.org, or set its executable path in ytgrab settings."
    if os.environ.get("TERMUX_VERSION"):
        return "Stock Termux does not provide the desktop VLC remote-control interface required by ytstream."
    return "sudo apt install vlc      (Debian/Ubuntu/Kali; or use your package manager / flatpak)"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def vlc_command(vlc, stream, start=0, port=None, fullscreen=False, extra=""):
    """Argument list that makes VLC play `stream` (no shell involved, so titles are safe)."""
    cmd = [vlc, "--play-and-exit", f"--meta-title={stream['title']}"]
    if stream.get("user_agent"):
        cmd.append(f"--http-user-agent={stream['user_agent']}")
    if start and start > 0 and not stream.get("is_live"):
        cmd.append(f"--start-time={int(start)}")
    if fullscreen:
        cmd.append("--fullscreen")
    if port:
        cmd += ["--extraintf=rc", f"--rc-host=127.0.0.1:{port}"]
    if extra:
        cmd += shlex.split(extra)
    cmd.append(stream["video_url"])
    if stream.get("audio_url"):
        cmd.append(f"--input-slave={stream['audio_url']}")
    return cmd


# ======================================================================
# VLC remote control (rc interface over a local socket)
# ======================================================================
class RcClient:
    """Tiny client for VLC's `rc` interface: `get_time`, `get_length`, `pause`, `seek N`, `quit`."""

    def __init__(self, port, host="127.0.0.1"):
        self.host, self.port = host, port
        self.sock = None
        self._lock = threading.Lock()

    def connect(self, timeout=10.0, alive=lambda: True):
        end = time.monotonic() + timeout
        while time.monotonic() < end and alive():
            try:
                s = socket.create_connection((self.host, self.port), timeout=0.5)
            except OSError:
                time.sleep(0.2)
                continue
            s.settimeout(0.4)
            self.sock = s
            self._drain()
            return True
        return False

    def _drain(self):
        try:
            while self.sock.recv(4096):
                pass
        except (socket.timeout, OSError):
            pass

    def _cmd(self, text):
        """Send a command, return the reply lines (prompts removed)."""
        with self._lock:
            if self.sock is None:
                return None
            try:
                self.sock.sendall((text + "\n").encode())
                buf = b""
                end = time.monotonic() + 0.6
                while time.monotonic() < end:
                    try:
                        chunk = self.sock.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        self.sock = None
                        break
                    buf += chunk
                    if buf.rstrip().endswith(b">"):
                        break
            except OSError:
                self.sock = None
                return None
        lines = [re.sub(r"^[>\s]+", "", ln).strip() for ln in buf.decode("utf-8", "replace").splitlines()]
        return [ln for ln in lines if ln]

    def _number(self, command):
        lines = self._cmd(command)
        for ln in reversed(lines or []):
            if re.fullmatch(r"\d+", ln):
                return int(ln)
        return None

    def get_time(self):
        return self._number("get_time")

    def get_length(self):
        return self._number("get_length")

    def pause(self):
        self._cmd("pause")

    def seek(self, seconds):
        self._cmd(f"seek {max(0, int(seconds))}")

    def quit(self):
        self._cmd("quit")

    def close(self):
        s, self.sock = self.sock, None
        if s:
            try:
                s.close()
            except OSError:
                pass


class PlaySession:
    """VLC running one video, with its position tracked in the background."""

    def __init__(self, cmd, port, popen=subprocess.Popen, rc_factory=RcClient, poll=0.5):
        self.cmd, self.port, self._popen, self._rc_factory = cmd, port, popen, rc_factory
        self.poll_every = poll
        self.proc = None
        self.rc = None
        self.pos = None             # last position VLC reported (seconds), None until known
        self.length = None
        self.rc_ok = False
        self.paused = False
        self.started = 0.0
        self._thread = None

    def start(self):
        self.proc = self._popen(self.cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        self.started = time.monotonic()
        self._thread = threading.Thread(target=self._track, daemon=True)
        self._thread.start()
        return self

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    @property
    def returncode(self):
        return None if self.proc is None else self.proc.poll()

    def _track(self):
        rc = self._rc_factory(self.port)
        if not rc.connect(timeout=12.0, alive=lambda: self.running):
            return
        self.rc, self.rc_ok = rc, True
        while self.running:
            t = rc.get_time()
            if t is not None:
                self.pos = t
            n = rc.get_length()
            if n:
                self.length = n
            time.sleep(self.poll_every)
        rc.close()

    # --- remote control
    def toggle_pause(self):
        if self.rc_ok and self.rc:
            self.rc.pause()
            self.paused = not self.paused

    def seek_by(self, delta):
        if self.rc_ok and self.rc and self.pos is not None:
            target = max(0, self.pos + delta)
            if self.length:
                target = min(target, max(0, self.length - 1))
            self.rc.seek(target)
            self.pos = target

    def stop(self):
        """Close VLC politely, then firmly. The last position stays available."""
        if self.rc_ok and self.rc and self.running:
            self.rc.quit()
        for _ in range(30):
            if not self.running:
                break
            time.sleep(0.1)
        if self.running:
            try:
                self.proc.terminate()
            except OSError:
                pass
            for _ in range(30):
                if not self.running:
                    break
                time.sleep(0.1)
        if self.running:
            try:
                self.proc.kill()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)
