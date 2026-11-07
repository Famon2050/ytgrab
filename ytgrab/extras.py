"""Extra features for ytgrab: thumbnails, clipboard, playlists."""

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlparse


def thumbnail_viewer():
    for name in ("chafa", "timg", "viu"):
        if shutil.which(name):
            return name
    return None


def thumbnail_available():
    return thumbnail_viewer() is not None


def _terminal_width(default=100):
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except Exception:
        return default


def _try_download(url, timeout=8):
    if not str(url).startswith(("https://", "http://")):     # never file:// or ftp://
        return None
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.youtube.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 - scheme checked above
            return r.read()
    except Exception:
        return None


def _candidate_thumbnails(info):
    vid = info.get("id")
    urls = []
    if vid:
        for name in ("maxresdefault", "sddefault", "hqdefault", "mqdefault"):
            urls.append(f"https://i.ytimg.com/vi/{vid}/{name}.jpg")
    t = info.get("thumbnail")
    if t:
        urls.append(t)
    return urls


def _fetch_best_thumbnail(info):
    for url in _candidate_thumbnails(info):
        data = _try_download(url)
        if data and len(data) > 1000:
            ext = ".webp" if url.endswith(".webp") else ".jpg"
            return data, ext
    return None, None


def show_thumbnail(info, target_w=100, target_h=50):
    viewer = thumbnail_viewer()
    if not viewer:
        return False

    if isinstance(info, dict):
        data, ext = _fetch_best_thumbnail(info)
        if not data:
            return False
    elif isinstance(info, str):
        data = _try_download(info)
        if not data:
            return False
        ext = ".webp" if info.endswith(".webp") else ".jpg"
    else:
        return False

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        cols = _terminal_width()
        width = max(40, min(target_w, cols - 4))

        if viewer == "chafa":
            subprocess.run(
                [
                    "chafa",
                    f"--size={width}x{target_h}",
                    "--format=auto",
                    "--symbols=block",
                    "--colors=full",
                    "--dither=none",
                    "--animate=off",
                    tmp_path,
                ],
                check=False,
            )
        elif viewer == "timg":
            subprocess.run(
                ["timg", "-g", f"{width}x{target_h}", "--upscale", tmp_path],
                check=False,
            )
        elif viewer == "viu":
            subprocess.run(["viu", "-w", str(width), tmp_path], check=False)
        return True
    except Exception:
        return False
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def clipboard_method():
    if os.name == "nt":
        return "powershell" if shutil.which("powershell") or shutil.which("pwsh") else None
    if os.environ.get("TERMUX_VERSION"):
        return "termux-clipboard-get" if shutil.which("termux-clipboard-get") else None
    if sys.platform == "darwin":
        return "pbpaste" if shutil.which("pbpaste") else None
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        return "wl-paste"
    if shutil.which("xclip"):
        return "xclip"
    if shutil.which("xsel"):
        return "xsel"
    if shutil.which("wl-paste"):
        return "wl-paste"
    return None


def clipboard_available():
    return clipboard_method() is not None


def clipboard_get(method=None):
    method = method or clipboard_method()
    if not method:
        return ""
    try:
        if method == "xclip":
            return subprocess.check_output(
                ["xclip", "-selection", "clipboard", "-o"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
        if method == "xsel":
            return subprocess.check_output(
                ["xsel", "-b", "-o"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
        if method == "wl-paste":
            return subprocess.check_output(
                ["wl-paste", "--no-newline"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
        if method == "pbpaste":
            return subprocess.check_output(["pbpaste"], text=True).strip()
        if method == "termux-clipboard-get":
            return subprocess.check_output([method], text=True, stderr=subprocess.DEVNULL).strip()
        if method == "powershell":
            exe = shutil.which("powershell") or shutil.which("pwsh")
            return subprocess.check_output([exe, "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                                           text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        pass
    return ""


_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
             "music.youtube.com", "youtube-nocookie.com",
             "www.youtube-nocookie.com"}
_YT_PATH_PREFIXES = ("/watch", "/playlist", "/shorts/", "/live/", "/embed/")


def is_youtube_url(text):
    """True for watch / playlist / shorts / live / embed / youtu.be links."""
    if not isinstance(text, str):
        return False
    text = text.strip()
    if not text.startswith(("http://", "https://")) or " " in text:
        return False
    try:
        u = urlparse(text)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if host == "youtu.be":
        return len(u.path.strip("/")) > 0
    if host in _YT_HOSTS:
        return u.path.startswith(_YT_PATH_PREFIXES)
    return False


def get_entries(info):
    out = []
    for i, e in enumerate(info.get("entries") or [], 1):
        if not e:
            continue
        url = e.get("url") or e.get("webpage_url") or ""
        if not url and e.get("id"):
            url = f"https://www.youtube.com/watch?v={e['id']}"
        out.append({
            "index": i,
            "id": e.get("id"),
            "title": e.get("title") or "(untitled)",
            "duration": e.get("duration") or 0,
            "url": url,
        })
    return out


def parse_selection(spec, total):
    spec = (spec or "").strip().lower()
    if not spec or spec in ("all", "a", "*"):
        return list(range(1, total + 1))
    result = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                start = int(a) if a.strip() else 1
                end = int(b) if b.strip() else total
            except ValueError:
                continue
            start = max(1, start)
            end = min(total, end)
            if start <= end:
                result.update(range(start, end + 1))
        else:
            try:
                n = int(part)
                if 1 <= n <= total:
                    result.add(n)
            except ValueError:
                continue
    return sorted(result)
