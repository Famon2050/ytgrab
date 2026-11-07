"""Interactive download flows: single, playlist, batch, queue, clipboard."""

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import failed
from .config import save_config, add_history
from .library import DupIndex, archive_add, archive_key, archive_set, find_existing, run_post_hook
from .extras import (
    clipboard_get, clipboard_method, get_entries, is_youtube_url,
    parse_selection, show_thumbnail,
)
from .fmt import fmt_duration, fmt_size, parse_clip_range, short_error
from .multi import Job, MultiDownloader
from .theme import C, banner, clear_screen, paint
from .ui import ask_yes_no, prompt, render_table, show_info
from .ytdl import (
    AUDIO_FORMATS, VIDEO_TARGETS,
    audio_menu, build_audio_opts, build_video_opts, do_download, playlist_template,
    fetch_info, fetch_info_quiet, file_size, get_filepath, has_playlist_param, notify,
    open_path, post_download_menu, strip_playlist_param, subtitle_menu, video_menu,
)


# ----------------------------------------------------------------------
# Subtitle helpers for batches
# ----------------------------------------------------------------------
def _find_subtitle_lang(info, preferred):
    """Return the best matching subtitle lang for this video, or None."""
    subs = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    langs = set(subs.keys()) | set(autos.keys())
    if not langs:
        return None
    if preferred in langs:
        return preferred
    for l in sorted(langs):
        if l.startswith(preferred + "-") or l.startswith(preferred + "_"):
            return l
    return None


def _prefetch_infos(urls, cfg, workers=4):
    """Fetch info for every URL (a few at a time). Unreadable URLs come back as
    None and are listed, so nothing disappears silently."""
    total = len(urls)
    infos, errors = [None] * total, {}
    if not total:
        return infos
    show = sys.stdout.isatty()
    with ThreadPoolExecutor(max_workers=min(workers, total)) as ex:
        futs = {ex.submit(fetch_info_quiet, u, cfg): i for i, u in enumerate(urls)}
        for n, f in enumerate(as_completed(futs), 1):
            i = futs[f]
            try:
                infos[i], err = f.result()
            except Exception as e:                     # never let one URL kill the batch
                infos[i], err = None, short_error(e)
            if infos[i] is None:
                errors[i] = err or "unknown error"
            if show:
                print(paint(f"  [{n}/{total}]  fetching info…", C.GREY), end="\033[K\r")
    if show:
        print("\033[K", end="")
    for i in sorted(errors):
        print(paint(f"  ✕  could not read {urls[i]}: {short_error(errors[i], 90)}", C.RED))
        try:
            failed.record(urls[i], error=short_error(errors[i]))
        except Exception:
            pass
    return infos


def _collect_subtitle_choices(infos, cfg, interactive=True):
    """When subtitles feature is on, prompt per-video but only for videos
    that actually have subs available. Returns {index: lang_or_None}."""
    choices = {}
    if not cfg.get("subtitles_enabled", False):
        return choices

    preferred = cfg.get("last_sub_lang", "en")
    for i, info in enumerate(infos, 1):
        if not info or info.get("_type") == "playlist":
            choices[i] = None
            continue
        lang = _find_subtitle_lang(info, preferred)
        if lang is None:
            print(paint(f"  [{i}] no subtitles available — skipping",
                        C.GREY_D))
            choices[i] = None
            continue
        title = info.get("title", "?")
        if len(title) > 50:
            title = title[:49] + "…"
        print()
        print(paint(f"  [{i}] ", C.RED, C.BOLD) + paint(title, C.WHITE))
        print(paint(f"       subtitles available: {lang}", C.GREY))
        if not interactive or ask_yes_no("     Download subtitles for this video?", default=False):
            choices[i] = lang
        else:
            choices[i] = None
    return choices


def _print_skipped(skipped, cfg, show=5):
    """“N already downloaded, skipped” followed by where the first few are."""
    print(paint(f"  ⏭  {len(skipped)} already downloaded, skipped:", C.GREY))
    try:
        idx = DupIndex()
    except Exception:
        idx = None
    for url, info in skipped[:show]:
        where = ""
        if idx is not None:
            try:
                d = find_existing(info, url, index=idx, scan=False)
            except Exception:
                d = None
            where = d["file"] if d and d["state"] == "exists" else ""
        title = str(info.get("title") or url)[:60]
        tail = paint("  →  ", C.GREY_D) + paint(where, C.WHITE) if where else ""
        print(paint(f"       • {title}", C.GREY) + tail)
    if len(skipped) > show:
        print(paint(f"       … and {len(skipped) - show} more", C.GREY_D))


def _build_jobs(infos, urls, kind, quality, sub_choices, cfg,
                outtmpls=None, use_archive=False, stats=None, overrides=None):
    """Turn a list of (url, info) into Job objects. With use_archive,
    videos already recorded in the archive are skipped. `stats` (a dict) gets
    "unavailable": how many URLs could not be read at all (they are failures).
    `overrides` (parallel to urls) may give {"kind", "quality"} per URL, e.g. a
    subscription that is always audio-only."""
    jobs, skipped = [], []
    stats = stats if stats is not None else {}
    stats.setdefault("unavailable", 0)
    seen = archive_set() if (use_archive and cfg.get("use_archive", True)) else set()
    for i, (url, info) in enumerate(zip(urls, infos), 1):
        if not info or info.get("_type") == "playlist":
            stats["unavailable"] += 1
            continue
        key = archive_key(info) or ""
        if key and key in seen:
            skipped.append((url, info))
            continue
        if key:
            seen.add(key)
        title = info.get("title") or f"video {i}"
        tmpl = outtmpls[i - 1] if outtmpls else None
        ov = (overrides[i - 1] if overrides else None) or {}
        jkind, jquality = ov.get("kind") or kind, ov.get("quality") or quality

        if jkind == "video":
            sub_lang = sub_choices.get(i)
            opts = build_video_opts(
                jquality, Path(cfg["download_dir"]), cfg,
                subtitles=bool(sub_lang), sub_lang=sub_lang or "en", outtmpl=tmpl,
            )
        else:
            opts = build_audio_opts(jquality, Path(cfg["download_dir"]), cfg,
                                    outtmpl=tmpl)

        jobs.append(Job(
            idx=i, url=url, title=title, kind=jkind, quality=jquality, opts=opts,
            channel=info.get("uploader") or info.get("channel") or "",
            archive_key=key,
        ))
    if skipped:
        _print_skipped(skipped, cfg)
    if stats["unavailable"]:
        print(paint(f"  ⚠  {stats['unavailable']} URL(s) could not be read and were not downloaded.", C.AMBER))
    return jobs


def _record_failed(j):
    """Remember a failed job so `ytgrab retry` can try it again. Never raises."""
    try:
        failed.record(j.url, title=j.title, error=j.error, kind=j.kind,
                      quality=j.quality, key=j.archive_key)
    except Exception:
        pass


def _record_done(cfg, j, quiet=False):
    """History + archive + notification + hook for a finished job.
    Returns False if the post-download hook failed, else True."""
    try:
        failed.remove([j.url])                      # it worked now: forget the old failure
    except Exception:
        pass
    add_history({
        "title": j.title, "url": j.url, "kind": j.kind,
        "quality": j.quality, "size": j.size, "channel": j.channel,
        "file": j.filepath, "date": time.strftime("%Y-%m-%d %H:%M"),
        "key": j.archive_key or "",
    })
    archive_add([j.archive_key])
    if cfg.get("notify", True):
        notify("ytgrab", f"Downloaded: {j.title[:60]}")
    return run_post_hook(cfg, j.filepath, title=j.title, url=j.url, kind=j.kind,
                         quiet=quiet) is not False


def _run_batch_jobs(cfg, jobs, interactive=True, done_urls=None):
    """Run jobs concurrently, record each one AS IT FINISHES (so a kill or crash
    mid-batch loses nothing), offer to retry failures.
    `done_urls` (a set) collects the URLs that succeeded. Returns (done, failed)."""
    if not jobs:
        print(paint("  Nothing to download.", C.GREY))
        return 0, 0
    workers = max(1, min(4, int(cfg.get("max_concurrent", 3) or 3)))
    done_total, pending = 0, jobs
    hook_failed, problems = [], []

    def finished(j):                                   # worker thread; must not print (live table)
        if j.status == "error":
            _record_failed(j)
            return
        if j.status != "done":
            return
        try:
            if not _record_done(cfg, j, quiet=True):
                hook_failed.append(j.title)
        except Exception as e:
            problems.append(short_error(e))
        if done_urls is not None:
            done_urls.add(j.url)

    while True:
        done, errs = MultiDownloader(pending, workers=workers, on_finish=finished).run()
        done_total += len(done)
        if hook_failed:
            print(paint(f"  ⚠  post-download hook failed for {len(hook_failed)} file(s).", C.AMBER))
            hook_failed.clear()
        if problems:
            print(paint(f"  ⚠  could not record history for some files: {problems[0]}", C.AMBER))
            problems.clear()
        if not errs:
            return done_total, 0
        if not interactive or not ask_yes_no(f"Retry {len(errs)} failed?", default=True):
            return done_total, len(errs)
        for j in errs:
            j.reset()
        pending = errs


# ----------------------------------------------------------------------
# Single download
# ----------------------------------------------------------------------
def _check_duplicate(cfg, info, url):
    """Tell the user when they already have this video and where it is.
    Returns True when the download should be skipped."""
    if not cfg.get("use_archive", True):
        return False
    try:
        dup = find_existing(info, url, download_dir=cfg.get("download_dir"))
    except Exception:
        return False                                # never let this block a download
    if not dup:
        return False
    state = dup["state"]
    print()
    if state in ("exists", "same-name"):
        head = ("Already downloaded." if state == "exists"
                else "A file with exactly this title is already in your downloads.")
        print(paint(f"  ⚠  {head}", C.AMBER, C.BOLD))
        print(paint("     Location  ", C.GREY) + paint(dup["file"], C.WHITE, C.BOLD))
        bits = [b for b in (fmt_size(dup["size"]) if dup["size"] else "", dup["date"]) if b]
        if bits and state == "exists":
            print(paint("     " + "  ·  ".join(bits), C.GREY_D))
        if state == "same-name":
            print(paint("     (matched by name only; it may be a different video with the same title)", C.GREY_D))
        path = Path(dup["file"])
        while True:
            print()
            raw = prompt("  [o]pen  [f]older  [d]ownload again anyway  Enter=skip  › ").lower()
            if raw == "o":
                open_path(path)
            elif raw == "f":
                open_path(path.parent)
            elif raw == "d":
                return False
            else:
                print(paint("  Skipped: you already have it.", C.GREY))
                return True
    if state == "missing":
        print(paint("  ℹ  You downloaded this before, but the file is no longer at", C.GREY))
        print(paint(f"     {dup['file']}", C.GREY_D))
        print(paint("     Downloading it again.", C.GREY))
    else:                                           # archived: remembered, location unknown
        print(paint("  ℹ  This video is in your archive, but ytgrab can't find the file. Downloading it again.", C.GREY))
    return False


def download_one(cfg, url, info=None):
    if info is None:
        print()
        info = fetch_info(url, cfg)
        if info is None:
            return False
        if info.get("_type") == "playlist":
            handle_playlist(cfg, info)
            return True

    clear_screen()
    banner(cfg)
    show_info(info)
    if cfg.get("thumb_preview"):
        show_thumbnail(info)

    if _check_duplicate(cfg, info, url):
        return True                                 # handled: nothing left to download

    if not ask_yes_no("Proceed with this video?", default=True):
        print(paint("  Skipped.", C.GREY)); return False

    print()
    type_rows = [(1, "Video", "MP4", "", False), (2, "Audio only", "", "", False)]
    default_type = 1 if cfg.get("last_kind", "video") == "video" else 2
    idx = render_table("Download type", type_rows, default_idx=default_type)
    kind = "video" if idx == 1 else "audio"
    cfg["last_kind"] = kind

    print()
    chosen_quality, chosen_size = None, None

    if kind == "video":
        rows, sizes = video_menu(info)
        default_idx = next((i for i, (k, _) in enumerate(VIDEO_TARGETS, 1)
                            if k == cfg.get("last_video_quality", "1080")), 2)
        idx = render_table("Quality", rows, default_idx=default_idx)
        q = VIDEO_TARGETS[idx - 1][0]
        cfg["last_video_quality"] = q
        chosen_quality, chosen_size = q, sizes.get(q)

        subtitles = False; sub_lang = cfg.get("last_sub_lang", "en")
        if cfg.get("subtitles_enabled", False):
            print()
            subs_rows = subtitle_menu(info)
            if subs_rows:
                if ask_yes_no("Download subtitles?", default=False):
                    di = next((i for i, (_, lang, _, _, _) in enumerate(subs_rows, 1)
                               if lang == sub_lang), 1)
                    sidx = render_table("Available subtitles", subs_rows, default_idx=di)
                    sub_lang = subs_rows[sidx - 1][1]; subtitles = True
                    cfg["last_sub_lang"] = sub_lang
            else:
                print(paint("  (no subtitles available for this video)", C.GREY_D))

        clip = None
        if cfg.get("clip_enabled", False):
            print()
            while True:
                raw = prompt("  Clip range?  [whole / MM:SS-MM:SS]  › ")
                if not raw or raw.lower() == "whole":
                    break
                try:
                    clip = parse_clip_range(raw)
                    break
                except ValueError as e:
                    print(paint(f"     !  {e}. Try again, or press Enter for the whole video.", C.AMBER))

        opts = build_video_opts(q, Path(cfg["download_dir"]), cfg,
                                subtitles=subtitles, sub_lang=sub_lang, clip=clip)
    else:
        rows, sizes = audio_menu(info)
        default_idx = next((i for i, (k, _, _) in enumerate(AUDIO_FORMATS, 1)
                            if k == cfg.get("last_audio_format", "mp3")), 1)
        idx = render_table("Audio format", rows, default_idx=default_idx)
        f = AUDIO_FORMATS[idx - 1][0]
        cfg["last_audio_format"] = f
        chosen_quality, chosen_size = f, sizes.get(f)
        opts = build_audio_opts(f, Path(cfg["download_dir"]), cfg)

    save_config(cfg)

    print()
    if chosen_size:
        print(paint(f"  About to download  ~{fmt_size(chosen_size)}", C.GREY))
    if not ask_yes_no("Confirm?", default=True):
        print(paint("  Cancelled.", C.GREY)); return False

    print()
    dl_info, cancelled, err = do_download(url, opts, cfg)
    if cancelled: return False
    if dl_info is None:                 # it failed: no history, no archive entry, no "Downloaded"
        try:
            failed.record(url, title=info.get("title", ""), error=short_error(err or "download failed"),
                          kind=kind, quality=chosen_quality or "", key=archive_key(info) or "")
        except Exception:
            pass
        print(paint("  Saved to the failed list.  Try again later with:  ytgrab retry", C.GREY_D))
        return False

    filepath = get_filepath(dl_info)
    add_history({
        "title": info.get("title", "?"), "url": url, "kind": kind,
        "quality": chosen_quality or "",
        "size": file_size(filepath) or chosen_size or 0,
        "channel": info.get("uploader") or info.get("channel") or "",
        "file": str(filepath or ""),
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "key": archive_key(info) or "",
    })
    archive_add([archive_key(info)])
    run_post_hook(cfg, filepath, title=info.get("title", ""), url=url, kind=kind)

    if cfg.get("notify", True):
        notify("ytgrab", f"Downloaded: {info.get('title', '?')[:60]}")

    post_download_menu(filepath, Path(cfg["download_dir"]))
    print()
    return True


# ----------------------------------------------------------------------
# Playlist
# ----------------------------------------------------------------------
def handle_playlist(cfg, info):
    entries = get_entries(info)
    if not entries:
        print(paint("  Playlist is empty.", C.RED))
        prompt("  Press enter to continue… ")
        return

    clear_screen()
    banner(cfg)
    title = info.get("title") or "playlist"
    print(paint(f"  📜  {title}", C.WHITE, C.BOLD))
    print(paint(f"      {len(entries)} videos", C.GREY))
    print()

    MAX_SHOW = 30
    for e in entries[:MAX_SHOW]:
        idx = paint(f"  {e['index']:>3}.  ", C.RED, C.BOLD)
        t = e["title"]
        if len(t) > 52: t = t[:51] + "…"
        dur = fmt_duration(e["duration"]) if e["duration"] else "?"
        print(idx + paint(f"{t:<54} ", C.OFF) + paint(dur.rjust(6), C.GREY))
    if len(entries) > MAX_SHOW:
        print(paint(f"  … and {len(entries) - MAX_SHOW} more", C.GREY_D))
    print()

    print(paint("  Selection:", C.GREY_D))
    print(paint("    all              every video", C.GREY))
    print(paint("    5                just #5", C.GREY))
    print(paint("    1,3,5-10         mixed", C.GREY))
    print()

    spec = prompt("  Selection [all]  › ")
    if spec.lower() in ("q", "quit"): return
    chosen = parse_selection(spec, len(entries))
    if not chosen:
        print(paint("  Nothing selected.", C.GREY))
        prompt("  Press enter to continue… "); return

    print()
    print(paint(f"  {len(chosen)} video(s) selected.", C.WHITE, C.BOLD))
    print()

    type_rows = [(1, "Video", "MP4", "", False), (2, "Audio only", "", "", False)]
    default_type = 1 if cfg.get("last_kind", "video") == "video" else 2
    idx = render_table("Download type", type_rows, default_idx=default_type)
    kind = "video" if idx == 1 else "audio"
    cfg["last_kind"] = kind

    print()
    if kind == "video":
        rows = [(i, label, "", "", False)
                for i, (_, label) in enumerate(VIDEO_TARGETS, 1)]
        di = next((i for i, (k, _) in enumerate(VIDEO_TARGETS, 1)
                   if k == cfg.get("last_video_quality", "1080")), 2)
        qi = render_table("Quality (applied to all)", rows, default_idx=di)
        quality = VIDEO_TARGETS[qi - 1][0]
        cfg["last_video_quality"] = quality
    else:
        rows = [(i, label, detail, "", False)
                for i, (_, label, detail) in enumerate(AUDIO_FORMATS, 1)]
        di = next((i for i, (k, _, _) in enumerate(AUDIO_FORMATS, 1)
                   if k == cfg.get("last_audio_format", "mp3")), 1)
        fi = render_table("Audio format (applied to all)", rows, default_idx=di)
        quality = AUDIO_FORMATS[fi - 1][0]
        cfg["last_audio_format"] = quality

    save_config(cfg)

    print()
    print(paint(f"  Ready to download {len(chosen)} video(s).", C.WHITE, C.BOLD))
    if not ask_yes_no("Start?", default=True):
        print(paint("  Cancelled.", C.GREY))
        prompt("  Press enter to continue… "); return

    # ---- prefetch + subtitle pre-check ----
    print()
    urls = [entries[n - 1]["url"] for n in chosen]
    infos = _prefetch_infos(urls, cfg)
    sub_choices = _collect_subtitle_choices(infos, cfg)

    outtmpls = None
    if cfg.get("playlist_folders", True):
        outtmpls = [playlist_template(Path(cfg["download_dir"]), title,
                                      entries[n - 1]["index"]) for n in chosen]
    jobs = _build_jobs(infos, urls, kind, quality, sub_choices, cfg,
                       outtmpls=outtmpls, use_archive=True)

    # ---- concurrent download ----
    print()
    _run_batch_jobs(cfg, jobs)
    print()
    prompt("  Press enter to continue… ")


# ----------------------------------------------------------------------
# Batch (used by queue file, pending, CLI --file)
# ----------------------------------------------------------------------
def batch_download(cfg, urls, outtmpls=None, use_archive=False,
                   interactive=True, infos=None, done_urls=None, overrides=None):
    """Download many URLs. `infos` may hold already-fetched metadata (None
    entries are fetched). Returns (done_count, failed_count); URLs that could not
    be read count as failed. `done_urls` (a set) collects the URLs that succeeded."""
    if not urls:
        print(paint("  Empty batch.", C.GREY))
        return 0, 0
    print()
    print(paint(f"  Preparing {len(urls)} URL(s)…", C.WHITE, C.BOLD))

    infos = list(infos) if infos is not None else [None] * len(urls)
    missing = [i for i, x in enumerate(infos) if x is None]
    for i, inf in zip(missing, _prefetch_infos([urls[i] for i in missing], cfg)):
        infos[i] = inf
    sub_choices = _collect_subtitle_choices(infos, cfg, interactive=interactive)

    kind = cfg.get("last_kind", "video")
    if kind == "video":
        quality = cfg.get("last_video_quality", "1080")
    else:
        quality = cfg.get("last_audio_format", "mp3")

    stats = {}
    jobs = _build_jobs(infos, urls, kind, quality, sub_choices, cfg,
                       outtmpls=outtmpls, use_archive=use_archive, stats=stats,
                       overrides=overrides)

    print()
    done, failed = _run_batch_jobs(cfg, jobs, interactive=interactive, done_urls=done_urls)
    print()
    return done, failed + stats.get("unavailable", 0)


def get_command(cfg, urls):
    """Non-interactive:  ytgrab get URL [URL...]  -> returns failure count."""
    expanded, tmpls, infos, failures = [], [], [], 0
    for u in urls:
        if not u.startswith(("http://", "https://")):
            print(paint(f"  Not a URL, skipped: {u}", C.RED)); failures += 1; continue
        info, err = fetch_info_quiet(u, cfg)
        if info is None:
            print(paint(f"  ✕  could not read {u}: {short_error(err or 'unknown error', 90)}", C.RED))
            _record_failed(Job(idx=0, url=u, title=u, kind="", quality="", opts={}, error=short_error(err or "")))
            failures += 1; continue
        if info.get("_type") == "playlist":
            title = info.get("title") or "playlist"
            for e in get_entries(info):
                expanded.append(e["url"]); infos.append(None)
                tmpls.append(playlist_template(Path(cfg["download_dir"]), title, e["index"])
                             if cfg.get("playlist_folders", True) else None)
        else:
            expanded.append(u); infos.append(info); tmpls.append(None)
    if not expanded:
        return failures
    _, failed = batch_download(cfg, expanded, outtmpls=tmpls, use_archive=True,
                               interactive=False, infos=infos)
    return failures + failed


# ----------------------------------------------------------------------
# Retry what failed earlier:  ytgrab retry [--list] [--clear]
# ----------------------------------------------------------------------
def retry_command(cfg, list_only=False, clear=False):
    """Returns a process exit code (0 = nothing left failing)."""
    if clear:
        print(paint(f"  ✓  Cleared {failed.clear()} failed download(s) from the log.", C.GREEN))
        return 0
    have = archive_set()
    stale = [it["url"] for it in failed.load() if it.get("key") and it["key"] in have]
    if stale:                                         # got downloaded some other way meanwhile
        failed.remove(stale)
    items = failed.load()
    if not items:
        print(paint("  No failed downloads. 🎉", C.GREY))
        return 0
    print(paint(f"  {len(items)} failed download(s)", C.WHITE, C.BOLD)); print()
    for i, it in enumerate(items, 1):
        t = str(it.get("title") or it.get("url"))
        print(paint(f"  {i:>3}.  ", C.RED, C.BOLD) + paint(t[:56], C.OFF)
              + paint(f"   {it.get('last_failed', '')}  ×{it.get('attempts', 1)}", C.GREY_D))
        if it.get("error"):
            print(paint(f"         {str(it['error'])[:90]}", C.GREY))
    if list_only:
        print(); print(paint("  Retry them with:  ytgrab retry", C.GREY_D))
        return 0
    print()
    _, still_failing = batch_download(cfg, [it["url"] for it in items], use_archive=True,
                                      interactive=False)
    return 1 if still_failing else 0


# ----------------------------------------------------------------------
# Interactive queue mode (paste a list, then download)
# ----------------------------------------------------------------------
def queue_mode(cfg):
    banner(cfg)
    print(paint("  📋  Queue mode", C.WHITE, C.BOLD)); print()
    print(paint("  Paste URLs one per line. Empty line to start.", C.GREY)); print()
    urls = []
    while True:
        line = prompt(f"  [{len(urls) + 1}]  › ")
        if not line: break
        if not (line.startswith("http://") or line.startswith("https://")):
            print(paint("  Not a URL, skipped.", C.RED)); continue
        if has_playlist_param(line):
            if not ask_yes_no("This URL has a playlist param. Keep it?", default=False):
                line = strip_playlist_param(line)
        urls.append(line)
    if not urls:
        print(paint("  Queue empty.", C.GREY)); prompt(""); return
    print()
    print(paint(f"  {len(urls)} URL(s) queued.", C.WHITE, C.BOLD))
    print()
    if not ask_yes_no("Start batch?", default=True): return
    batch_download(cfg, urls)
    prompt("  Press enter to return… ")


def batch_from_file(cfg, path):
    p = Path(path)
    if not p.is_file():
        print(paint(f"  File not found: {p}", C.RED)); return
    urls = []
    for line in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        if not (line.startswith("http://") or line.startswith("https://")): continue
        if has_playlist_param(line): line = strip_playlist_param(line)
        urls.append(line)
    if not urls:
        print(paint("  No URLs found in file.", C.RED)); return
    print(paint(f"  {len(urls)} URLs loaded from {p}", C.WHITE, C.BOLD))
    if not ask_yes_no("Start batch?", default=True): return
    batch_download(cfg, urls, use_archive=True)


# ----------------------------------------------------------------------
# Clipboard watch — queues URLs instead of downloading them
# ----------------------------------------------------------------------
def clipboard_watch(cfg):
    method = clipboard_method()
    if not method:
        print()
        print(paint("  ✕  No clipboard tool found.", C.RED))
        print(paint("     Install one of:  xclip  ·  xsel  ·  wl-clipboard", C.GREY))
        print(paint("     Debian/Kali:     sudo apt install xclip", C.GREY))
        print(); return

    banner(cfg)
    print(paint("  📋  Clipboard watch mode", C.WHITE, C.BOLD)); print()
    print(paint("  Reading clipboard via:  ", C.GREY)
          + paint(method, C.WHITE, C.BOLD))
    print(paint("  Copy any YouTube URL to add it to the pending queue.", C.GREY))
    print(paint("  Press Ctrl+C to stop, then 'p' at main prompt to review.", C.GREY_D))
    print()

    last = clipboard_get(method)            # re-copying a link later must still work

    try:
        while True:
            time.sleep(0.8)
            content = clipboard_get(method)
            if not content or content == last: continue
            last = content
            if not is_youtube_url(content): continue

            clean = content
            if has_playlist_param(clean):
                if not ask_yes_no("Keep playlist param?", default=False):
                    clean = strip_playlist_param(clean)

            info = fetch_info(clean, cfg)
            title = (info or {}).get("title", "") or clean
            from .pending import add as queue_add, count as queue_count
            if queue_add(clean, title=title):
                n = queue_count()
                print()
                print(paint("  ✓  Queued", C.GREEN, C.BOLD)
                      + paint(f"  ·  {n} item(s) waiting", C.GREY))
                print(paint(f"     {title[:70]}", C.OFF))
            else:
                print(paint("  •  Already in queue.", C.GREY_D))
    except KeyboardInterrupt:
        print()
        print(paint("  Stopped watching.", C.GREY))


# ----------------------------------------------------------------------
# Process pending queue
# ----------------------------------------------------------------------
def process_queue(cfg):
    from .pending import (
        all_items as queue_all,
        remove_by_url as queue_remove,
        clear as queue_clear,
    )
    items = queue_all()
    clear_screen()
    banner(cfg)

    print(paint("  📥  Pending queue", C.WHITE, C.BOLD))
    print()

    if not items:
        print(paint("  Queue is empty.", C.GREY))
        print(paint("  Run 'ytgrab watch' to collect links from your clipboard.",
                    C.GREY_D))
        print()
        prompt("  Press enter to return… ")
        return

    print(paint(f"  {len(items)} item(s) waiting:", C.GREY))
    print()
    for i, it in enumerate(items, 1):
        t = it.get("title") or it.get("url", "?")
        if len(t) > 54:
            t = t[:53] + "…"
        idx = paint(f"  {i:>3}.", C.RED, C.BOLD)
        print(idx + "  " + paint(t, C.OFF))
    print()

    print(paint("  Selection:", C.GREY_D))
    print(paint("    all              download everything with current defaults", C.GREY))
    print(paint("    s                step through one by one (interactive)", C.GREY))
    print(paint("    1,3,5            specific items", C.GREY))
    print(paint("    1-3              ranges", C.GREY))
    print(paint("    x 2              remove #2 from queue", C.GREY))
    print(paint("    c                clear entire queue", C.GREY))
    print(paint("    Enter            back", C.GREY))
    print()

    spec = prompt("  Choose  › ").strip()
    if not spec:
        return

    low = spec.lower()

    if low == "c":
        if ask_yes_no("Clear the entire queue?", default=False):
            queue_clear()
            print(paint("  ✓  Queue cleared.", C.GREEN))
            prompt("")
        return

    if low == "x" or low.startswith("x "):
        parts = low.split()
        targets = parts[1:] if len(parts) > 1 else [prompt("  Remove which #?  › ").strip()]
        removed = 0
        for t in targets:
            if t.isdigit() and 1 <= int(t) <= len(items):
                url = items[int(t) - 1]["url"]
                if queue_remove(url):
                    removed += 1
        print(paint(f"  ✓  Removed {removed} item(s).", C.GREEN))
        prompt("")
        return

    if low == "s":
        selected = list(range(1, len(items) + 1))
        step_mode = True
    elif low == "all":
        selected = list(range(1, len(items) + 1))
        step_mode = False
    else:
        selected = parse_selection(spec, len(items))
        if not selected:
            print(paint("  Nothing selected.", C.GREY))
            prompt(""); return
        step_mode = False

    if step_mode:
        done = 0
        for i, n in enumerate(selected, 1):
            item = items[n - 1]
            print()
            print(paint(f"  [{i}/{len(selected)}]  ", C.AMBER, C.BOLD)
                  + paint(item["title"][:60], C.OFF))
            print()
            if download_one(cfg, item["url"]):
                queue_remove(item["url"])
                done += 1
        print()
        print(paint(f"  ✓  Done: {done}/{len(selected)} succeeded.", C.GREEN, C.BOLD))
        prompt("  Press enter to return… ")
        return

    # batch mode via concurrent engine
    urls = [items[n - 1]["url"] for n in selected]
    ok_urls = set()
    batch_download(cfg, urls, done_urls=ok_urls)

    from .pending import remove_urls as queue_remove_urls
    removed = queue_remove_urls(ok_urls)             # failed / cancelled / unreadable items stay queued
    print(paint(f"  ✓  Removed {removed} finished item(s) from queue.", C.GREEN))
    if len(urls) - removed > 0:
        print(paint(f"     {len(urls) - removed} item(s) stay in the queue (failed, cancelled or unreadable).", C.AMBER))
    prompt("  Press enter to return… ")
