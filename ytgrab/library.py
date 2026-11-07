"""Download archive (skip what you already have) and post-download hooks."""

import os
import re
import shlex
import subprocess
from pathlib import Path

import yt_dlp.utils

from .config import CONFIG_DIR, file_lock, load_history  # noqa: F401
from .theme import C, paint

ARCHIVE_FILE = CONFIG_DIR / "archive.txt"


# ----------------------------------------------------------------------
# Archive: one "<extractor> <id>" line per downloaded video
# ----------------------------------------------------------------------
def archive_key(info):
    if not info or not info.get("id"):
        return None
    return f"{str(info.get('extractor_key') or 'youtube').lower()} {info['id']}"


def key_for_entry(entry):
    """Archive key for a flat playlist/channel entry (always YouTube)."""
    return archive_key({"id": entry.get("id"), "extractor_key": "youtube"})


def archive_set():
    try:
        return {ln.strip() for ln in
                ARCHIVE_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()}
    except OSError:
        return set()


def archive_add(keys):
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return
    with file_lock(ARCHIVE_FILE):
        have = archive_set()
        new = [k for k in keys if k not in have]
        if not new:
            return
        ARCHIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ARCHIVE_FILE.open("a", encoding="utf-8") as f:
            f.write("\n".join(new) + "\n")
            f.flush()
            os.fsync(f.fileno())


# ----------------------------------------------------------------------
# "Do I already have this?"  -> where is the file
# ----------------------------------------------------------------------
_YT_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})")
_NOT_MEDIA = {".part", ".ytdl", ".temp", ".tmp", ".json", ".jpg", ".jpeg", ".png", ".webp",
              ".vtt", ".srt", ".description", ".txt"}
_MEDIA_EXTENSIONS = {
    ".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".flv",
    ".mpg", ".mpeg", ".3gp", ".ts", ".m2ts", ".mp3", ".m4a", ".opus",
    ".wav", ".flac", ".ogg", ".aac", ".wma",
}
_SCAN_LIMIT = 60000                 # files looked at while searching the download folder


def iter_media(root, max_files=_SCAN_LIMIT):
    """Every media file under `root` as (Path, size, mtime). Skips hidden folders,
    half-downloads, thumbnails and subtitles."""
    seen = 0
    try:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                suffix = os.path.splitext(name)[1].lower()
                if suffix in _NOT_MEDIA or suffix not in _MEDIA_EXTENSIONS or name.startswith("."):
                    continue
                seen += 1
                if seen > max_files:
                    return
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size > 0:
                    yield p, st.st_size, st.st_mtime
    except OSError:
        return


def _key_from_url(url):
    m = _YT_ID.search(url or "")
    return f"youtube {m.group(1)}" if m else None


class DupIndex:
    """History lookups built once, so checking 500 videos reads the file once.
    Newer history entries win. Old entries (before 1.5.2) have no stored key, so
    theirs is worked out from the URL."""

    def __init__(self, history=None):
        self.by_key, self.by_url = {}, {}
        for h in (load_history() if history is None else history):
            if not isinstance(h, dict):
                continue
            key = h.get("key") or _key_from_url(h.get("url"))
            if key:
                self.by_key[key] = h
            if h.get("url"):
                self.by_url[h["url"]] = h

    def lookup(self, info=None, url=""):
        key = archive_key(info) if info else None
        key = key or _key_from_url(url)
        hit = self.by_key.get(key) if key else None
        for u in (url, (info or {}).get("webpage_url")):
            hit = hit or (self.by_url.get(u) if u else None)
        return hit


def _existing_file(path):
    """The file at `path`, or the same name with another extension (a conversion
    or merge may have changed it); None when it is gone."""
    if not path:
        return None
    p = Path(path)
    try:
        if p.is_file():
            return p if p.suffix.lower() in _MEDIA_EXTENSIONS else None
        for sib in p.parent.glob(glob_escape(p.stem) + ".*"):
            if sib.suffix.lower() in _MEDIA_EXTENSIONS and sib.is_file():
                return sib
    except OSError:
        pass
    return None


def glob_escape(s):
    return re.sub(r"([\[\]*?])", r"[\1]", s)


def scan_download_dir(root, title, max_depth=3):
    """A media file in `root` (and playlist sub-folders) named exactly like `title`.
    Used when the history has no record, e.g. the file was moved or came from an
    older ytgrab. Matches by NAME only, so callers must word it as "same name"."""
    if not root or not title:
        return None
    want = yt_dlp.utils.sanitize_filename(title).strip().lower()
    if not want:
        return None
    clipped = len(want.encode("utf-8")) >= 150     # the file name template cuts titles at 150 bytes
    root = Path(root)
    seen = 0
    try:
        for dirpath, dirs, files in os.walk(root):
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth >= max_depth:
                dirs[:] = []
            for name in files:
                seen += 1
                if seen > _SCAN_LIMIT:
                    return None
                stem, ext = os.path.splitext(name)
                if ext.lower() not in _MEDIA_EXTENSIONS:
                    continue
                stem = re.sub(r"^\d{1,4} - ", "", stem).strip().lower()   # playlist numbering
                if stem == want or (clipped and stem and want.startswith(stem) and len(stem) > 100):
                    return Path(dirpath) / name
    except OSError:
        pass
    return None


def find_existing(info, url="", download_dir=None, index=None, scan=True):
    """Do we already have this video?  Returns None when it looks new, else a dict:
        state  "exists"   the file is on disk right now          (file = its path)
               "same-name" no record, but a file with this exact title is there
               "missing"  we downloaded it before, the file is gone (file = where it was)
               "archived" only the archive remembers it (no location known)
        file, title, date, size, kind
    """
    idx = index or DupIndex()
    h = idx.lookup(info, url)
    title = (info or {}).get("title") or (h or {}).get("title") or ""
    out = {"file": "", "title": title, "date": "", "size": 0, "kind": ""}
    if h:
        out.update(date=h.get("date", ""), size=h.get("size") or 0, kind=h.get("kind", ""),
                   title=h.get("title") or title)
        f = _existing_file(h.get("file"))
        if f is None and scan:
            f = scan_download_dir(download_dir, title)
        if f is not None:
            out.update(state="exists", file=str(f))
            return out
        out.update(state="missing", file=h.get("file") or "")
        return out
    if scan:
        f = scan_download_dir(download_dir, title)
        if f is not None:
            out.update(state="same-name", file=str(f))
            return out
    key = archive_key(info) if info else None
    if key and key in archive_set():
        out["state"] = "archived"
        return out
    return None


def archive_clear():
    try:
        ARCHIVE_FILE.unlink()
        return True
    except OSError:
        return False


def cmd_archive(action):
    if action == "clear":
        n = len(archive_set())
        archive_clear()
        print(paint(f"  ✓  Archive cleared ({n} entries).", C.GREEN))
        return
    print(paint(f"  Archive: {ARCHIVE_FILE}", C.GREY))
    print(paint(f"  {len(archive_set())} video(s) recorded.", C.WHITE, C.BOLD))
    print(paint("  Playlists, batch files, `get` and `sync` skip these.", C.GREY_D))
    print(paint("  Reset with:  ytgrab archive clear", C.GREY_D))


# ----------------------------------------------------------------------
# Post-download hook (never goes through a shell)
# ----------------------------------------------------------------------
def hook_argv(template, path):
    """Split the user's command into argv. `{file}` is replaced by the path;
    if the template has no `{file}`, the path is appended as the last arg."""
    argv = shlex.split(template, posix=(os.name != "nt"))
    if not argv:
        return []
    p = str(path)
    if any("{file}" in a for a in argv):
        return [a.replace("{file}", p) for a in argv]
    return argv + [p]


def run_post_hook(cfg, path, title="", url="", kind="", quiet=False):
    """Run cfg['post_hook'] for a finished file. Returns True/False, or None
    if no hook is configured. Title/URL are passed as environment variables
    (YTGRAB_FILE, YTGRAB_TITLE, YTGRAB_URL, YTGRAB_KIND), never interpolated."""
    template = (cfg.get("post_hook") or "").strip()
    if not template or not path:
        return None
    try:
        argv = hook_argv(template, path)
    except ValueError as e:
        if not quiet: print(paint(f"  ⚠  Bad post-download hook: {e}", C.AMBER))
        return False
    env = dict(os.environ, YTGRAB_FILE=str(path), YTGRAB_TITLE=str(title),
               YTGRAB_URL=str(url), YTGRAB_KIND=str(kind))
    try:
        r = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        if not quiet: print(paint(f"  ⚠  Hook failed to run: {e}", C.AMBER))
        return False
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
        if not quiet: print(paint(f"  ⚠  Hook exited {r.returncode}: {tail[0][:80]}", C.AMBER))
        return False
    return True
