"""yt-dlp wrapper: options, fetch, download, notifications."""

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

try:
    import yt_dlp
    from yt_dlp.utils import download_range_func
except ImportError:
    print("Missing dependency. Install with:  pip install yt-dlp")
    sys.exit(1)

from .config import save_config, COOKIES_FILE
from .fmt import codec_friendly, fmt_size, parse_rate, short_error
from .theme import C, paint
from .progress import ProgressRenderer
from .ui import Spinner, ask_yes_no, prompt


VIDEO_TARGETS = [
    ("best", "Best available"), ("1080", "1080p Full HD"), ("720", "720p HD"),
    ("480", "480p"), ("360", "360p"),
]

AUDIO_FORMATS = [
    ("mp3", "MP3", "universal"), ("m4a", "M4A", "native"),
    ("opus", "Opus", "small, high quality"), ("wav", "WAV", "uncompressed"),
]

BANNER_SIZES = [
    ("large", "Large   ·  huge gradient block letters that fill the frame (auto-fits your window)"),
    ("classic", "Classic ·  the original smaller shadow letters"),
    ("compact", "Compact ·  one framed line"),
    ("minimal", "Minimal ·  one colored line, no frame"),
]

PRESETS = {
    "music": {"last_kind": "audio", "last_audio_format": "mp3"},
    "song":  {"last_kind": "audio", "last_audio_format": "mp3"},
    "movie": {"last_kind": "video", "last_video_quality": "1080"},
    "hd":    {"last_kind": "video", "last_video_quality": "1080"},
    "clip":  {"last_kind": "video", "last_video_quality": "720"},
    "small": {"last_kind": "video", "last_video_quality": "480"},
    "best":  {"last_kind": "video", "last_video_quality": "best"},
}

SPONSOR_CATS = ["sponsor", "intro", "outro", "selfpromo", "interaction", "music_offtopic"]
SPONSOR_LEVELS = {
    "off": [],
    "sponsors": ["sponsor"],
    "standard": ["sponsor", "selfpromo", "interaction"],
    "all": list(SPONSOR_CATS),
}
SPONSOR_LABELS = [
    ("off", "Off", "keep everything"),
    ("sponsors", "Sponsors only", "paid promotions"),
    ("standard", "Sponsors + self-promo + reminders", "“like & subscribe” bits too"),
    ("all", "Everything", "also intros, outros and off-topic music"),
]


def sponsor_level(cfg):
    """The SponsorBlock level of a config. Older versions stored True/False."""
    v = cfg.get("sponsorblock")
    if v is True:
        return "all"                                  # what True always meant
    return v if isinstance(v, str) and v in SPONSOR_LEVELS else "off"

PLAYER_CLIENTS = [
    ("", "Auto (default)"), ("android", "Android"), ("ios", "iOS"),
    ("web", "Web"), ("tv", "TV"), ("web_safari", "Web Safari"),
]

BROWSER_OPTIONS = [
    ("", "None"), ("firefox", "Firefox"), ("chrome", "Chrome"),
    ("chromium", "Chromium"), ("brave", "Brave"), ("edge", "Edge"),
    ("opera", "Opera"), ("vivaldi", "Vivaldi"),
]

COOKIES_SOURCES = [
    ("file", "File  ·  ~/.config/ytgrab/cookies.txt"),
    ("browser", "Browser  ·  read directly from a browser profile"),
    ("none", "None  ·  disabled"),
]


def _tmpl_dir(out_dir):
    """Directory as it must appear inside a yt-dlp output template."""
    return str(out_dir).replace("%", "%%")


def safe_folder(name):
    """Folder-safe, template-safe name (a literal % must be doubled)."""
    from yt_dlp.utils import sanitize_filename
    s = sanitize_filename(str(name or ""), restricted=False).strip(". ")
    return (s or "untitled")[:80].replace("%", "%%")


def default_template(out_dir):
    return os.path.join(_tmpl_dir(out_dir), "%(title).150B.%(ext)s")


def _quality_template(template):
    """Insert the actual selected height into a video filename, before ext."""
    marker = ".%(ext)s"
    if marker not in template:
        return template
    return template.replace(marker, "%(height& {}p)s" + marker, 1)


def _video_container(cfg):
    """Validate the persisted video container; legacy or invalid values use MP4."""
    value = str((cfg or {}).get("video_container", "mp4")).strip().lower()
    return value if value in ("mp4", "mkv") else "mp4"


def playlist_template(out_dir, folder, index):
    return os.path.join(_tmpl_dir(out_dir), safe_folder(folder),
                        f"{int(index):03d} - %(title).150B.%(ext)s")


def channel_template(out_dir, channel):
    return os.path.join(_tmpl_dir(out_dir), safe_folder(channel),
                        "%(title).150B.%(ext)s")


def _res(f):
    """What yt-dlp's `res` sort key means: the shorter side, so a 720x1280
    Short is "720p" (and it matches what is actually downloaded)."""
    w, h = f.get("width") or 0, f.get("height") or 0
    return min(w, h) if w and h else h


def _pick_video(formats, target):
    best, score = None, None
    for f in formats:
        if f.get("vcodec", "none") == "none": continue
        h = _res(f)
        if target is not None and h > target: continue
        s = (h, f.get("fps") or 0, f.get("tbr") or 0)
        if score is None or s > score: score, best = s, f
    return best


def _pick_audio(formats):
    best, abr_max = None, -1
    for f in formats:
        if f.get("acodec", "none") == "none": continue
        if f.get("vcodec", "none") != "none": continue
        abr = f.get("abr") or 0
        if abr > abr_max: abr_max, best = abr, f
    return best


def _estimate_bytes(fmt, duration):
    if not fmt: return None
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if size: return size
    if duration:
        tbr = fmt.get("tbr")
        if tbr: return int(tbr * 1000 / 8 * duration)
    return None


def video_menu(info):
    formats = info.get("formats") or []
    duration = info.get("duration") or 0
    audio = _pick_audio(formats)
    audio_size = _estimate_bytes(audio, duration) or 0
    rows, sizes = [], {}
    for i, (key, base_label) in enumerate(VIDEO_TARGETS, 1):
        target = None if key == "best" else int(key)
        v = _pick_video(formats, target)
        if not v:
            rows.append((i, base_label, "", "", True)); continue
        vsize = _estimate_bytes(v, duration)
        total = (vsize + audio_size) if vsize else None
        details = []
        if _res(v): details.append(f"{_res(v)}p")
        if v.get("fps"): details.append(f"{v['fps']:g}fps")
        codec = codec_friendly(v.get("vcodec", ""))
        if codec: details.append(codec)
        rows.append((i, base_label, " · ".join(details), fmt_size(total) if total else "?", False))
        sizes[key] = total
    return rows, sizes


def audio_menu(info):
    formats = info.get("formats") or []
    duration = info.get("duration") or 0
    audio = _pick_audio(formats)
    src = _estimate_bytes(audio, duration)
    rows, sizes = [], {}
    for i, (key, label, detail) in enumerate(AUDIO_FORMATS, 1):
        if key == "mp3": size = int(duration * 245_000 / 8) if duration else src
        elif key in ("m4a", "opus"): size = src
        elif key == "wav": size = int(duration * 1_411_000 / 8) if duration else None
        else: size = None
        rows.append((i, label, detail, fmt_size(size) if size else "?", False))
        sizes[key] = size
    return rows, sizes


def subtitle_menu(info):
    subs = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    rows = []; idx = 1
    for lang in sorted(subs.keys()):
        rows.append((idx, lang, "manual", "", False)); idx += 1
    for lang in sorted(autos.keys()):
        if lang in subs: continue
        rows.append((idx, lang, "auto-generated", "", False)); idx += 1
    if not rows: return None
    return rows[:24]


def cookies_file_ok():
    try:
        if not COOKIES_FILE.is_file(): return False
        return COOKIES_FILE.stat().st_size > 100
    except OSError:
        return False


def _apply_auth(opts, cfg):
    src = cfg.get("cookies_source", "file")
    if src == "file" and cookies_file_ok():
        opts["cookiefile"] = str(COOKIES_FILE)
    elif src == "browser" and cfg.get("cookies_from_browser"):
        opts["cookiesfrombrowser"] = (cfg["cookies_from_browser"],)
    if cfg.get("player_client"):
        opts.setdefault("extractor_args", {})
        opts["extractor_args"].setdefault("youtube", {})
        opts["extractor_args"]["youtube"]["player_client"] = [cfg["player_client"]]
    lim = cfg.get("speed_limit", "")
    if lim:
        bps = parse_rate(lim)
        if bps: opts["ratelimit"] = bps


def _apply_sponsorblock(opts, cfg):
    """SponsorBlock via real postprocessors. (``sponsorblock_remove`` is a
    CLI-only key that YoutubeDL ignores, so setting it did nothing.)
    Only used for actual downloads, never for metadata fetches."""
    cats = SPONSOR_LEVELS[sponsor_level(cfg)]
    if not cats:
        return
    pps = opts.setdefault("postprocessors", [])
    pps[0:0] = [
        {"key": "SponsorBlock", "categories": list(cats), "when": "after_filter"},
        {"key": "ModifyChapters", "remove_sponsor_segments": list(cats),
         "remove_chapters_patterns": [], "remove_ranges": [],
         "sponsorblock_chapter_title": "[SponsorBlock]: %(category_names)l",
         "force_keyframes": False},
    ]


def _apply_chapters(opts, cfg):
    """Split-by-chapters: besides the full video, one file per chapter in a
    folder named after the video ("001 - Intro.mp4", "002 - …")."""
    if not cfg.get("split_chapters"):
        return
    opts.setdefault("postprocessors", []).append(
        {"key": "FFmpegSplitChapters", "force_keyframes": False})
    default = opts["outtmpl"]
    opts["outtmpl"] = {
        "default": default,
        "chapter": os.path.join(os.path.dirname(default), "%(title).150B",
                                "%(section_number)03d - %(section_title).100B%(height&{}p)s.%(ext)s"),
    }


def _apply_common(opts, cfg):
    """Retries + resume of partial downloads."""
    try:
        r = max(0, int(cfg.get("retries", 10)))
    except (TypeError, ValueError):
        r = 10
    opts.update({"retries": r, "fragment_retries": r,
                 "extractor_retries": min(r, 3), "continuedl": True,
                 "socket_timeout": 30})


def _extract_with_auth(url, opts, download):
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=download), None
    except Exception as e:
        msg = str(e).lower()
        has_cookie = "cookiefile" in opts or "cookiesfrombrowser" in opts
        if has_cookie and ("cookie" in msg or "keyring" in msg or "profile" in msg):
            opts2 = dict(opts)
            opts2.pop("cookiefile", None)
            opts2.pop("cookiesfrombrowser", None)
            try:
                with yt_dlp.YoutubeDL(opts2) as ydl:
                    return ydl.extract_info(url, download=download), None
            except Exception as e2:
                return None, str(e2)
        return None, str(e)


class _SilentLogger:
    """yt-dlp must not write to the terminal while the bar is up; ytgrab prints
    the one clean error line itself."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _attach_progress(opts, cfg=None):
    """Give `opts` its own single-bar progress renderer (hooks for the
    download itself and for the final merge/convert step)."""
    r = ProgressRenderer(style=(cfg or {}).get("bar_style", "classic"),
                         details=(cfg or {}).get("show_speed_eta", True))
    opts["progress_hooks"] = [r.hook]
    opts["postprocessor_hooks"] = [r.postprocessor_hook]
    opts.update({"quiet": True, "no_warnings": True, "noprogress": True,
                 "logger": _SilentLogger()})
    return opts


def _renderers(opts):
    return [r for r in (getattr(h, "__self__", None) for h in opts.get("progress_hooks") or [])
            if isinstance(r, ProgressRenderer)]


def build_video_opts(quality, out_dir, cfg, subtitles=False, sub_lang="en", clip=None,
                     outtmpl=None):
    # `res` in format_sort is min(width, height): a 720x1280 Short is "720p", whereas a
    # `height<=720` filter would wrongly push it down to ~360p.
    sort = ["res"] if quality == "best" else [f"res:{quality}"]
    if cfg.get("prefer_compat", True):
        sort += ["vcodec:h264", "acodec:aac"]     # same resolution -> the MP4 that plays everywhere
    container = _video_container(cfg)
    opts = {
        "format": "bestvideo*+bestaudio/best", "format_sort": sort,
        "merge_output_format": container,
        "outtmpl": _quality_template(outtmpl or default_template(out_dir)),
        "outtmpl_na_placeholder": "",
    }
    if container == "mkv":
        # Remux even single-file sources so MKV is respected.
        opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mkv"}]
    _attach_progress(opts, cfg)
    if subtitles:
        opts.update({"writesubtitles": True, "writeautomaticsub": True,
                     "subtitleslangs": [sub_lang], "subtitlesformat": "srt"})
    if clip:
        start, end = clip
        opts["download_ranges"] = download_range_func(None, [(start, end)])
        opts["force_keyframes_at_cuts"] = True
    pps = list(opts.get("postprocessors", []))
    if cfg.get("embed_metadata", True):
        pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})
    if subtitles and cfg.get("embed_subs", True):
        # already_have_subtitle=False -> loose .srt files are removed after embedding
        pps.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
    if pps:
        opts["postprocessors"] = pps
    _apply_common(opts, cfg)
    _apply_auth(opts, cfg)
    _apply_sponsorblock(opts, cfg)
    _apply_chapters(opts, cfg)
    return opts


def build_audio_opts(fmt, out_dir, cfg, outtmpl=None):
    opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl or default_template(out_dir),
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": fmt,
             "preferredquality": "0" if fmt == "mp3" else None},
            {"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True},
        ],
    }
    _attach_progress(opts, cfg)
    # Only download the thumbnail when it will be embedded; otherwise
    # opus/wav downloads left a stray .webp next to the audio file.
    if fmt in ("mp3", "m4a"):
        opts["writethumbnail"] = True
        opts["postprocessors"].append({"key": "EmbedThumbnail"})
    _apply_common(opts, cfg)
    _apply_auth(opts, cfg)
    _apply_sponsorblock(opts, cfg)
    _apply_chapters(opts, cfg)
    return opts


def fetch_info_quiet(url, cfg):
    """Like fetch_info but silent (no spinner / printing). Returns (info, err)."""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist"}
    _apply_auth(opts, cfg)
    return _extract_with_auth(url, opts, download=False)


def fetch_info(url, cfg):
    sp = Spinner("Fetching video info"); sp.start()
    # extract_flat: playlists open instantly instead of resolving every video.
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist"}
    _apply_auth(opts, cfg)
    info, err = _extract_with_auth(url, opts, download=False)
    sp.stop()
    if err:
        print(paint(f"  Could not fetch info: {err}", C.RED))
        return None
    return info


def _finish_bars(bars, ok):
    for r in bars:
        r.finish() if ok else r.close()


def do_download(url, opts, cfg):
    """Download one URL with the single progress bar.
    Returns (info, cancelled, error): `info` is None when it failed or was
    cancelled, and `error` then says why (empty when cancelled)."""
    bars = _renderers(opts)
    for r in bars:
        r.start()                                   # pulse while yt-dlp reads the page
    ok, hint, err, info = False, False, "", None
    try:
        info, err = _extract_with_auth(url, opts, download=True)
        if err is not None and "403" in err and not cookies_file_ok() \
                and not cfg.get("cookies_from_browser"):
            _finish_bars(bars, False)               # clear the bar before talking
            print()
            print(paint("  ⚠  HTTP 403 Forbidden.", C.AMBER, C.BOLD))
            print(paint("     Trying fallback player client (android)…", C.GREY))
            fallback = dict(opts)
            fallback["extractor_args"] = {k: dict(v) for k, v in (opts.get("extractor_args") or {}).items()}
            fallback["extractor_args"].setdefault("youtube", {})["player_client"] = ["android"]
            _attach_progress(fallback, cfg)         # fresh bar for the second attempt
            bars = _renderers(fallback)
            for r in bars:
                r.start()
            try:
                with yt_dlp.YoutubeDL(fallback) as ydl:
                    info, err = ydl.extract_info(url, download=True), None
            except Exception as e2:
                info, err, hint = None, f"Fallback failed: {short_error(e2)}", True
        ok = err is None
    except KeyboardInterrupt:
        _finish_bars(bars, False)
        print()
        print(paint("  ✕  Download cancelled.", C.AMBER, C.BOLD))
        return None, True, ""
    finally:
        _finish_bars(bars, ok)
    if not ok:
        print(paint(f"\n  Error: {err}", C.RED))
        if hint:
            print(paint("  → Run: ytgrab cookies-help", C.AMBER))
        return None, False, err
    return info, False, ""


def get_filepath(info):
    if not info: return None
    # After merge / post-processing, top-level filepath is the deliverable.
    # requested_downloads may still name a temporary component file.
    fp = info.get("filepath") or info.get("_filename")
    if fp: return Path(fp)
    reqs = info.get("requested_downloads") or []
    for item in reqs:
        if item.get("filepath"):
            return Path(item["filepath"])
    return None


def file_size(path):
    """Real size on disk in bytes, or 0 if unknown."""
    try:
        return Path(path).stat().st_size if path else 0
    except OSError:
        return 0


def _as_escape(s):
    """Escape for an AppleScript string literal."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _spawn(cmd):
    """Run a command with an argument list (never through a shell) and
    detach from the terminal. Video titles are attacker-controlled, so they
    must never be interpolated into a shell string."""
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def notify(title, body):
    title, body = str(title), str(body)
    if os.environ.get("TERMUX_VERSION") and shutil.which("termux-notification"):
        _spawn(["termux-notification", "--title", title, "--content", body])
    elif sys.platform.startswith("darwin"):
        script = (f'display notification "{_as_escape(body)}" '
                  f'with title "{_as_escape(title)}"')
        _spawn(["osascript", "-e", script])
    elif shutil.which("notify-send"):
        _spawn(["notify-send", "--", title, body])


def open_path(p):
    p = str(p)
    if os.environ.get("TERMUX_VERSION") and shutil.which("termux-open"):
        _spawn(["termux-open", p])
    elif sys.platform.startswith("darwin"):
        _spawn(["open", p])
    elif os.name == "nt":
        try: os.startfile(p)  # type: ignore
        except Exception: pass
    else:
        _spawn(["xdg-open", p])


def post_download_menu(filepath, folder):
    while True:
        print()
        print(paint("  ✓  ", C.GREEN, C.BOLD) + paint("Saved to ", C.GREY)
              + paint(str(folder), C.WHITE, C.BOLD))
        if filepath: print(paint(f"     {filepath.name}", C.OFF))
        print()
        raw = prompt("  [o]pen  [f]older  Enter=next  › ").lower()
        if raw == "o" and filepath and filepath.exists(): open_path(filepath)
        elif raw == "f": open_path(folder)
        else: return


def has_playlist_param(url):
    try: return "list" in parse_qs(urlparse(url).query)
    except Exception: return False


def strip_playlist_param(url):
    try:
        p = urlparse(url); q = parse_qs(p.query, keep_blank_values=False)
        for k in ("list", "index", "start_radio", "pp"):
            q.pop(k, None)
        return p._replace(query=urlencode(q, doseq=True)).geturl()
    except Exception:
        return url


def check_ytdlp_age():
    v = getattr(yt_dlp.version, "__version__", "")
    try:
        parts = v.split(".")
        d = datetime(int(parts[0]), int(parts[1]), int(parts[2]))
        return (datetime.now() - d).days, v
    except Exception:
        return None, v


def upgrade_ytdlp():
    """Upgrade yt-dlp inside the environment ytgrab itself runs in.
    (`pipx upgrade yt-dlp` only works if yt-dlp is its own pipx app, which it
    is not when it was pulled in as a dependency of ytgrab.)"""
    spec = "yt-dlp[default]"
    cmd = [sys.executable, "-m", "pip", "install", "-U", spec]
    print("  " + paint("Running: " + " ".join(cmd), C.GREY))
    try:
        if subprocess.run(cmd).returncode == 0:
            return True
    except OSError:
        pass
    if shutil.which("pipx"):
        cmd = ["pipx", "runpip", "ytgrab", "install", "-U", spec]
        print("  " + paint("Retrying: " + " ".join(cmd), C.GREY))
        try:
            return subprocess.run(cmd).returncode == 0
        except OSError:
            pass
    print(paint("  Could not upgrade automatically. Try:  pipx upgrade ytgrab", C.AMBER))
    return False


def maybe_update_ytdlp(cfg):
    if not cfg.get("check_ytdlp", True): return
    age, ver = check_ytdlp_age()
    if age is None or age < 30: return
    print()
    print(paint(f"  ⚠  yt-dlp is {age} days old  (v{ver}).", C.AMBER, C.BOLD))
    if ask_yes_no("Upgrade yt-dlp now?", default=True):
        print(); upgrade_ytdlp(); print()


PROFILE_KEYS = ("last_kind", "last_video_quality", "last_audio_format", "video_container",
                "last_sub_lang", "subtitles_enabled", "sponsorblock", "split_chapters", "speed_limit")
_RESERVED_NAMES = ("list", "save", "delete")


def apply_preset(cfg, name, persist=True):
    """Apply a built-in preset or one of the user's saved profiles.
    persist=False changes this run only (the `-p` flag), not the saved settings."""
    name = name.lower()
    data = PRESETS.get(name) or (cfg.get("profiles") or {}).get(name)
    if not data: return False
    cfg.update({k: v for k, v in data.items() if k in PROFILE_KEYS and v is not None})
    if persist:
        save_config(cfg)
    return True


def save_profile(cfg, name):
    """Save the current type/quality/format/subtitle/SponsorBlock/limit
    settings as a named profile. Returns (ok, message)."""
    name = (name or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,24}", name):
        return False, "Use 1-24 letters, digits, - or _."
    if name in PRESETS or name in _RESERVED_NAMES:
        return False, f"'{name}' is reserved."
    cfg.setdefault("profiles", {})[name] = {k: cfg.get(k) for k in PROFILE_KEYS}
    save_config(cfg)
    return True, f"Profile '{name}' saved."


def delete_profile(cfg, name):
    name = (name or "").strip().lower()
    if name in (cfg.get("profiles") or {}):
        del cfg["profiles"][name]; save_config(cfg); return True
    return False
