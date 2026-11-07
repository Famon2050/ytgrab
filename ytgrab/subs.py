"""Channel subscriptions:  ytgrab sub add <channel>   then   ytgrab sync"""

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import CONFIG_DIR, atomic_write_text, file_lock, read_json, release_exclusive, try_exclusive  # noqa: F401
from .extras import get_entries
from .library import archive_add, archive_set, key_for_entry
from .theme import C, paint
from .ui import Spinner, ask_yes_no
from .ytdl import AUDIO_FORMATS, VIDEO_TARGETS, _apply_auth, _extract_with_auth, channel_template

SUBS_FILE = CONFIG_DIR / "subs.json"
_CHANNEL_RE = re.compile(r"^/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)/?$")


def normalize_channel_url(url):
    """@handle / channel URL  ->  its /videos tab (newest first)."""
    url = (url or "").strip()
    if url.startswith("@"):
        url = "https://www.youtube.com/" + url
    try:
        u = urlparse(url)
    except ValueError:
        return url
    host = (u.hostname or "").lower()
    if (host == "youtube.com" or host.endswith(".youtube.com")) \
            and _CHANNEL_RE.match(u.path):
        return u._replace(path=u.path.rstrip("/") + "/videos",
                          query="", fragment="").geturl()
    return url


def load():
    return read_json(SUBS_FILE, [], list)


def save(subs):
    atomic_write_text(SUBS_FILE, json.dumps(subs, indent=2, ensure_ascii=False))


def fetch_recent(url, cfg, n=15):
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist", "playlistend": n}
    _apply_auth(opts, cfg)
    return _extract_with_auth(url, opts, download=False)


def _clean_title(t):
    return re.sub(r"\s+-\s+Videos$", "", str(t or "")).strip()


def is_channel_url(url):
    """True for a channel home / handle URL (not a video or playlist)."""
    return normalize_channel_url(url) != (url or "").strip().rstrip("/") \
        or bool(re.search(r"youtube\.com/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)/videos/?$",
                          url or ""))


def subscribe(cfg, target, mark_seen):
    """Silent subscribe. Returns (ok, message, title, entries)."""
    url = normalize_channel_url(target)
    if not url.startswith(("http://", "https://")):
        return False, "Give a channel URL or an @handle.", "", []
    if any(s.get("url") == url for s in load()):
        return False, "Already subscribed.", "", []
    info, err = fetch_recent(url, cfg, n=200)          # slow: done outside the lock
    if err or not info:
        return False, f"Could not read channel: {err}", "", []
    title = _clean_title(info.get("title") or info.get("uploader")) or url
    entries = get_entries(info)
    if mark_seen:
        archive_add([key_for_entry(e) for e in entries])
    with file_lock(SUBS_FILE):
        subs = load()
        if any(s.get("url") == url for s in subs):
            return False, "Already subscribed.", "", []
        subs.append({"url": url, "title": title,
                     "added": time.strftime("%Y-%m-%d %H:%M"), "last_sync": ""})
        save(subs)
    return True, f"Subscribed to {title}", title, entries


def find_new(cfg, sub, seen=None, depth=None):
    """Unseen recent uploads of one subscription. Returns (entries, error).

    Pages back (depth, 2*depth, 4*depth...) until the window contains a video you
    already have, so a channel that posted more than `depth` videos since your last
    sync is not silently missed. The very first sync stays shallow (`depth` only):
    it must never bulk-download a whole back catalogue."""
    seen = archive_set() if seen is None else seen
    depth = int(depth or cfg.get("sync_depth", 15) or 15)
    cap = max(depth, int(cfg.get("sync_max_depth", 300) or 300))
    page_back = bool(sub.get("last_sync"))
    n = depth
    while True:
        info, err = fetch_recent(sub["url"], cfg, n)
        if err or not info:
            return [], str(err)
        entries = get_entries(info)
        if (not page_back or len(entries) < n or n >= cap
                or any(key_for_entry(e) in seen for e in entries)):
            break
        n = min(n * 2, cap)
    return [e for e in entries if key_for_entry(e) not in seen], ""


# ----------------------------------------------------------------------
# Per-subscription settings and filters
#   ytgrab sub set 2 audio=mp3 min=10 exclude='live|reaction'
# ----------------------------------------------------------------------
SETTING_HELP = (
    "video=Q|audio=FMT|format=default   download as video (best/1080/720/480/360) or audio (mp3/m4a/opus/wav)\n"
    "    min=MINUTES  max=MINUTES            only videos at least / at most this long (off = no limit)\n"
    "    include=REGEX  exclude=REGEX        title must / must not match (case-insensitive; off = none)\n"
    "    reset                               clear every setting of this subscription"
)
_FIELDS = ("kind", "quality", "min_minutes", "max_minutes", "include", "exclude")


def parse_sub_settings(tokens):
    """['audio=mp3', 'min=10'] -> ({'kind': 'audio', 'quality': 'mp3', 'min_minutes': 10.0}, [errors]).
    A value of None means "remove this setting"."""
    updates, errors = {}, []
    video_keys = [k for k, _ in VIDEO_TARGETS]
    audio_keys = [k for k, _, _ in AUDIO_FORMATS]
    for tok in tokens or []:
        if tok.lower() in ("reset", "clear"):
            updates = {f: None for f in _FIELDS}
            continue
        key, sep, val = tok.partition("=")
        key, val = key.strip().lower(), val.strip()
        if not sep:
            errors.append(f"'{tok}': use key=value (see below)"); continue
        off = val.lower() in ("off", "none", "no", "")
        if key == "video":
            if val in video_keys: updates.update(kind="video", quality=val)
            else: errors.append(f"video={val}: pick one of {', '.join(video_keys)}")
        elif key == "audio":
            if val.lower() in audio_keys: updates.update(kind="audio", quality=val.lower())
            else: errors.append(f"audio={val}: pick one of {', '.join(audio_keys)}")
        elif key == "format":
            if val.lower() == "default": updates.update(kind=None, quality=None)
            else: errors.append("format: only 'format=default' is valid")
        elif key in ("min", "max"):
            if off:
                updates[key + "_minutes"] = None
            else:
                try:
                    num = float(val)
                    if num <= 0: raise ValueError
                    updates[key + "_minutes"] = num
                except ValueError:
                    errors.append(f"{key}={val}: needs a positive number of minutes")
        elif key in ("include", "exclude"):
            if off:
                updates[key] = None
            else:
                try:
                    re.compile(val)
                    updates[key] = val
                except re.error as e:
                    errors.append(f"{key}: bad regular expression ({e})")
        else:
            errors.append(f"unknown setting '{key}'")
    mn, mx = updates.get("min_minutes"), updates.get("max_minutes")
    if mn and mx and mx < mn:
        errors.append("max is smaller than min")
    return updates, errors


def describe_sub(sub):
    """One short line summarising a subscription's own settings ('' if none)."""
    bits = []
    if sub.get("kind") and sub.get("quality"):
        bits.append(f"{sub['kind']} {sub['quality']}")
    mn, mx = sub.get("min_minutes"), sub.get("max_minutes")
    if mn or mx:
        bits.append(f"{mn:g}-{mx:g} min" if mn and mx else (f">= {mn:g} min" if mn else f"<= {mx:g} min"))
    if sub.get("include"): bits.append(f"include /{sub['include']}/")
    if sub.get("exclude"): bits.append(f"exclude /{sub['exclude']}/")
    return " · ".join(bits)


def override_for(sub):
    """(kind, quality) when the subscription has its own valid format, else (None, None)."""
    kind, q = sub.get("kind"), sub.get("quality")
    if kind == "video" and q in [k for k, _ in VIDEO_TARGETS]: return kind, q
    if kind == "audio" and q in [k for k, _, _ in AUDIO_FORMATS]: return kind, q
    return None, None


def _matches(pattern, text, on_error):
    """Case-insensitive regex search. A hand-edited, invalid pattern is ignored (`on_error`
    is what it should count as: True for include, False for exclude) rather than dropping videos."""
    try:
        return bool(re.search(pattern, text, re.I))
    except re.error:
        return on_error


def apply_filters(entries, sub):
    """Split entries into (wanted, skipped) using the subscription's filters.
    Videos with unknown length are kept (live / premiere entries have none)."""
    inc, exc = sub.get("include"), sub.get("exclude")
    mn, mx = sub.get("min_minutes"), sub.get("max_minutes")
    keep, skipped = [], []
    for e in entries:
        title, dur = e.get("title") or "", e.get("duration") or 0
        ok = True
        if inc and not _matches(inc, title, True): ok = False
        if exc and _matches(exc, title, False): ok = False
        if dur and mn and dur < mn * 60: ok = False
        if dur and mx and dur > mx * 60: ok = False
        (keep if ok else skipped).append(e)
    return keep, skipped


def plan_sub(cfg, sub, seen=None, depth=None):
    """New uploads of a subscription after its filters: (to_download, skipped, error)."""
    new, err = find_new(cfg, sub, seen, depth)
    if err:
        return [], [], err
    keep, skipped = apply_filters(new, sub)
    return keep, skipped, ""


def cmd_sub_set(target, tokens):
    """ytgrab sub set <# | name> key=value ...   Returns True on success."""
    updates, errors = parse_sub_settings(tokens)
    if not tokens:
        errors.append("nothing to set")
    if errors:
        for m in errors:
            print(paint(f"  ✕  {m}", C.RED))
        print(paint("\n  Settings:\n    " + SETTING_HELP, C.GREY_D))
        return False
    with file_lock(SUBS_FILE):
        subs = load()
        t = (target or "").strip().lower()
        hit = None
        if t.isdigit() and 1 <= int(t) <= len(subs):
            hit = int(t) - 1
        else:
            for i, s in enumerate(subs):
                if t and (t in s.get("title", "").lower() or t in s.get("url", "").lower()):
                    hit = i; break
        if hit is None:
            print(paint("  No such subscription.", C.RED)); return False
        for k, v in updates.items():
            if v is None: subs[hit].pop(k, None)
            else: subs[hit][k] = v
        save(subs)
        sub = subs[hit]
    print(paint(f"  ✓  {sub.get('title', '?')}: ", C.GREEN) + (describe_sub(sub) or "default settings"))
    print(paint("     Filters apply to future uploads; skipped videos are marked as seen.", C.GREY_D))
    return True


def cmd_sub_add(cfg, target):
    url = normalize_channel_url(target)
    if not url.startswith(("http://", "https://")):
        print(paint("  Give a channel URL or an @handle.", C.RED)); return False
    if any(s.get("url") == url for s in load()):
        print(paint("  Already subscribed.", C.GREY)); return False
    mark_seen = ask_yes_no("Only download NEW uploads from now on?  "
                           "(existing videos get marked as seen)", default=True)
    sp = Spinner("Looking up channel"); sp.start()
    ok, msg, title, _ = subscribe(cfg, target, mark_seen)
    sp.stop()
    if not ok:
        print(paint(f"  {msg}", C.RED)); return False
    print(paint(f"  ✓  Subscribed to {title}", C.GREEN))
    print(paint("     Fetch new uploads any time with:  ytgrab sync", C.GREY_D))
    return True


def cmd_sub_list():
    subs = load()
    if not subs:
        print(paint("  No subscriptions.  Add one:  ytgrab sub add @channel", C.GREY))
        return
    print(paint(f"  {len(subs)} subscription(s)", C.WHITE, C.BOLD)); print()
    for i, s in enumerate(subs, 1):
        last = s.get("last_sync") or "never"
        print(paint(f"  {i:>2}.  ", C.RED, C.BOLD) + paint(s.get("title", "?"), C.OFF)
              + paint(f"   last sync: {last}", C.GREY_D))
        extra = describe_sub(s)
        if extra:
            print(paint(f"        {extra}", C.AMBER))


def remove_sub(target):
    """Silent remove by 1-based number, title or URL. Returns the title or None."""
    with file_lock(SUBS_FILE):
        return _remove_sub_locked(target)


def _remove_sub_locked(target):
    subs = load()
    t = (target or "").strip().lower()
    hit = None
    if t.isdigit() and 1 <= int(t) <= len(subs):
        hit = int(t) - 1
    else:
        for i, s in enumerate(subs):
            if t and (t in s.get("title", "").lower() or t in s.get("url", "").lower()):
                hit = i; break
    if hit is None:
        return None
    gone = subs.pop(hit); save(subs)
    return gone.get("title", "?")


def cmd_sub_remove(target):
    title = remove_sub(target)
    if title is None:
        print(paint("  No such subscription.", C.RED)); return False
    print(paint(f"  ✓  Removed {title}", C.GREEN))
    return True


def sync(cfg, dry_run=False):
    """Download every unseen recent upload. Returns number of failures.
    dry_run: only report what would be downloaded; changes nothing."""
    from .flows import batch_download
    # channels saved from the home dashboard ("my channels") are for exploring; they carry
    # auto=False and are never downloaded behind your back
    subs = [s for s in load() if s.get("auto") is not False]
    if not subs:
        print(paint("  No subscriptions.  Add one:  ytgrab sub add @channel", C.GREY))
        return 0
    guard = None
    if not dry_run:
        guard = try_exclusive("sync")             # overlapping cron runs would fetch the same files twice
        if guard is None:
            print(paint("  Another ytgrab sync is already running; skipping this run.", C.AMBER))
            return 0
    try:
        seen = archive_set()
        depth = int(cfg.get("sync_depth", 15) or 15)
        urls, tmpls, overrides, failures = [], [], [], 0
        queued = set()                                # a video can appear in two subscriptions
        synced = []
        for s in subs:
            print(paint("  ↻  ", C.RED, C.BOLD) + paint(s.get("title", "?"), C.OFF))
            new, skipped, err = plan_sub(cfg, s, seen, depth)
            if err:
                print(paint(f"     lookup failed: {err[:70]}", C.RED))
                failures += 1; continue
            tmpl = channel_template(Path(cfg["download_dir"]), s.get("title", "channel"))
            kind, quality = override_for(s)
            ov = {"kind": kind, "quality": quality} if kind else None
            added = 0
            for e in new:
                if e["url"] in queued:
                    continue
                queued.add(e["url"])
                urls.append(e["url"]); tmpls.append(tmpl); overrides.append(ov); added += 1
                if dry_run:
                    dur = f"  ({e['duration'] // 60} min)" if e.get("duration") else ""
                    print(paint(f"       + {str(e.get('title'))[:60]}{dur}", C.GREY))
            if skipped and not dry_run:               # remember them, so they are not re-checked every run
                archive_add([key_for_entry(e) for e in skipped])
            synced.append(s.get("url"))
            note = f"     {added} new" + (f", {len(skipped)} filtered out" if skipped else "")
            if ov:
                note += f"  [{kind} {quality}]"
            print(paint(note, C.GREY))
        if dry_run:
            print(); print(paint(f"  Dry run: {len(urls)} video(s) would be downloaded. Nothing was changed.", C.WHITE, C.BOLD))
            return failures
        stamp = time.strftime("%Y-%m-%d %H:%M")
        with file_lock(SUBS_FILE):                    # re-read: don't clobber edits made meanwhile
            stored = load()
            for s in stored:
                if s.get("url") in synced:
                    s["last_sync"] = stamp
            save(stored)
        if not urls:
            print(); print(paint("  ✓  Everything is up to date.", C.GREEN)); return failures
        _, failed = batch_download(cfg, urls, outtmpls=tmpls, use_archive=True,
                                   interactive=False, overrides=overrides)
        return failures + failed
    finally:
        if guard is not None:
            release_exclusive(guard)
