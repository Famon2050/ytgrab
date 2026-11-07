"""Config + history storage for ytgrab."""

import contextlib
import copy
import json
import os
import tempfile
import threading
import time
from pathlib import Path

try:                                    # POSIX process locks
    import fcntl
except ImportError:                     # pragma: no cover
    fcntl = None
try:                                    # Windows byte-range locks
    import msvcrt
except ImportError:                     # pragma: no cover
    msvcrt = None

if os.name == "nt":
    CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "ytgrab"
else:
    CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ytgrab"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
COOKIES_FILE = CONFIG_DIR / "cookies.txt"

DEFAULTS = {
    "download_dir": str(Path.home() / "Downloads"),
    "last_kind": "video",
    "last_video_quality": "1080",
    "video_container": "mp4",  # video output container: mp4 or mkv
    "last_audio_format": "mp3",
    "last_sub_lang": "en",
    "subtitles_enabled": False,
    "clip_enabled": False,
    "banner_size": "large",
    "theme": "youtube",
    "sponsorblock": False,      # off / sponsors / standard / all  (True = all, from older versions)
    "split_chapters": False,    # also write one file per chapter, next to the full video
    "bar_style": "classic",     # progress bar look: ytgrab bar-style
    "show_speed_eta": True,     # speed + ETA next to the progress bar
    "notify": True,
    "speed_limit": "",
    "stream_quality": "1080",   # ytstream: highest quality to stream (best/1080/720/480/360)
    "stream_fullscreen": False,
    "stream_resume": True,      # offer to continue where you stopped
    "stream_player": "",        # path to VLC when it is not on PATH
    "stream_args": "",          # extra VLC options, e.g. --aout=alsa
    "check_ytdlp": True,
    "max_concurrent": 3,
    "cookies_source": "file",
    "cookies_from_browser": "",
    "player_client": "",
    "use_archive": True,        # skip already-downloaded videos in playlists/batches/sync
    "playlist_folders": True,   # playlists go into their own numbered folder
    "embed_subs": True,         # embed (instead of leaving loose .srt files)
    "embed_metadata": True,     # title/uploader/chapters into the file
    "retries": 10,
    "post_hook": "",            # command run after each download; {file} = path
    "prefer_compat": True,      # prefer h264/AAC at equal resolution (MP4 plays everywhere)
    "thumb_preview": False,     # show the thumbnail on the info card
    "sync_depth": 15,           # how many recent uploads `ytgrab sync` looks at per page
    "sync_max_depth": 300,      # sync pages back (15, 30, 60...) until it reaches a known video, up to this many
    "profiles": {},             # user presets saved with `ytgrab preset save`
}


# ----------------------------------------------------------------------
# Safe storage helpers: atomic writes, corruption quarantine, locks
# ----------------------------------------------------------------------
def atomic_write_text(path, text, mode=None):
    """Write `text` to `path` so readers never see a half-written file.
    Temp file in the same directory + fsync + os.replace. New files are
    private (0600) unless `mode` says otherwise."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600 if mode is None else mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _quarantine(path, keep=3):
    """Move an unreadable data file aside so the next save cannot destroy it."""
    path = Path(path)
    try:
        path.rename(path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"))
        old = sorted(path.parent.glob(path.name + ".corrupt-*"))
        for p in old[:-keep]:
            p.unlink()
    except OSError:
        pass


def read_json(path, default, kind=None):
    """Parse a JSON file. Missing -> default. Corrupt (or wrong type) -> the file
    is quarantined as `<name>.corrupt-<time>` and default is returned."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if kind is not None and not isinstance(data, kind):
            raise ValueError("unexpected JSON type")
        return data
    except FileNotFoundError:
        return default
    except ValueError:                  # JSONDecodeError / UnicodeDecodeError / wrong type
        _quarantine(path)
        return default
    except OSError:
        return default


class _PathLock:
    def __init__(self):
        self.rlock = threading.RLock()
        self.depth = 0
        self.fd = None


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


@contextlib.contextmanager
def file_lock(path):
    """Mutual exclusion for read-modify-write of `path`, across threads AND
    processes (the TUI and a cron `ytgrab sync` can run together). Re-entrant."""
    path = Path(path)
    with _LOCKS_GUARD:
        st = _LOCKS.setdefault(str(path), _PathLock())
    with st.rlock:
        st.depth += 1
        try:
            if st.depth == 1 and (fcntl is not None or msvcrt is not None):
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    st.fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
                    if fcntl is not None:
                        fcntl.flock(st.fd, fcntl.LOCK_EX)
                    else:
                        if os.fstat(st.fd).st_size == 0:
                            os.write(st.fd, b"\0")
                        os.lseek(st.fd, 0, os.SEEK_SET)
                        msvcrt.locking(st.fd, msvcrt.LK_LOCK, 1)
                except OSError:
                    if st.fd is not None:
                        with contextlib.suppress(OSError): os.close(st.fd)
                    st.fd = None        # read-only dir etc.: thread lock only
            yield
        finally:
            if st.depth == 1 and st.fd is not None:
                with contextlib.suppress(OSError):
                    if fcntl is not None:
                        fcntl.flock(st.fd, fcntl.LOCK_UN)
                    else:
                        os.lseek(st.fd, 0, os.SEEK_SET)
                        msvcrt.locking(st.fd, msvcrt.LK_UNLCK, 1)
                    os.close(st.fd)
                st.fd = None
            st.depth -= 1


def try_exclusive(name):
    """Non-blocking process-wide lock (e.g. one `ytgrab sync` at a time).
    Returns a handle to keep alive, or None if someone else holds it."""
    if fcntl is None and msvcrt is None:
        return object()
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(CONFIG_DIR / f"{name}.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return object()
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        os.close(fd)
        return None
    return fd


def release_exclusive(handle):
    if isinstance(handle, int):
        with contextlib.suppress(OSError):
            os.close(handle)                # closing drops the flock


def harden_permissions():
    """Config dir 0700 and cookies.txt 0600 (it holds live session cookies).
    Also fixes installs made by older versions."""
    for p, mode in ((CONFIG_DIR, 0o700), (COOKIES_FILE, 0o600)):
        try:
            if p.exists():
                os.chmod(p, mode)
        except OSError:
            pass


def _fresh_defaults():
    d = dict(DEFAULTS)
    d["profiles"] = {}          # don't share the nested dict with DEFAULTS
    return d


_BASELINE = {}      # what this process last loaded/saved, to tell its own edits from others'


def load_config():
    data = read_json(CONFIG_FILE, None, dict)
    merged = _fresh_defaults()
    if data is not None:
        merged.update(data)
    if not isinstance(merged.get("profiles"), dict):
        merged["profiles"] = {}
    _BASELINE.clear()
    _BASELINE.update(copy.deepcopy(merged))
    return merged


def save_config(cfg):
    """Save settings without clobbering other running copies: only the keys THIS
    process changed (since it loaded/saved) are written over what is on disk, so
    the TUI and a CLI run no longer undo each other's changes."""
    with file_lock(CONFIG_FILE):
        disk = read_json(CONFIG_FILE, None, dict) if _BASELINE else None
        out = dict(disk) if disk is not None else {}
        for k, v in cfg.items():
            if disk is None or k not in _BASELINE or _BASELINE[k] != v:
                out[k] = v
        atomic_write_text(CONFIG_FILE, json.dumps(out, indent=2))
        _BASELINE.clear()
        _BASELINE.update(copy.deepcopy(out))
    cfg.update(out)                     # pick up what other processes changed


def load_history():
    return read_json(HISTORY_FILE, [], list)


def save_history(hist):
    atomic_write_text(HISTORY_FILE, json.dumps(hist, indent=2, ensure_ascii=False))


def add_history(entry):
    with file_lock(HISTORY_FILE):      # the TUI records from worker threads; cron from another process
        hist = load_history()
        hist.append(entry)
        save_history(hist)
