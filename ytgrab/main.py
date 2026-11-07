#!/usr/bin/env python3
"""ytgrab - a modern CLI YouTube downloader."""

import argparse
import sys
from pathlib import Path

from .config import harden_permissions, load_config, save_history
from .flows import (
    batch_from_file, clipboard_watch, download_one, get_command, handle_playlist,
    process_queue, queue_mode, retry_command,
)
from .library import cmd_archive
from . import home, theme
from .dupes import dupes_command
from .finder import find_command
from .pending import count as queue_count
from . import __version__
from .fmt import parse_rate
from .stats import stats_dashboard
from .search import search_flow
from .subs import cmd_sub_add, cmd_sub_list, cmd_sub_remove, cmd_sub_set, sync as sync_subs
from .settings import (
    cmd_bar_style, cmd_cookies_clear, cmd_cookies_import, cmd_doctor, cmd_set_dir,
    cmd_set_preset, cmd_set_player_client, cmd_set_theme, cookies_help,
    settings_menu, show_history,
)
from .theme import C, THEME_ORDER, apply_theme, banner, paint
from .ui import Back, ask_yes_no, prompt, render_table
from .ytdl import (
    AUDIO_FORMATS, SPONSOR_LEVELS, VIDEO_TARGETS,
    apply_preset, fetch_info, has_playlist_param, maybe_update_ytdlp,
    strip_playlist_param, upgrade_ytdlp,
)


def parse_args():
    p = argparse.ArgumentParser(prog="ytgrab",
                                description="Modern CLI YouTube downloader.")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("settings", help="Open settings")
    sub.add_parser("history", help="Show download history")
    sub.add_parser("channels", help="My channels: saved creators to explore (no YouTube account)")
    p_stream = sub.add_parser("stream", help="Watch in VLC without downloading (same as the ytstream command)")
    p_stream.add_argument("target", nargs="*", help="a YouTube link, or words to search for")
    p_stream.add_argument("-q", "--quality", choices=["best", "1080", "720", "480", "360"], help="highest quality")
    sub.add_parser("clear-history", help="Clear download history")
    sub.add_parser("queue", help="Batch mode: paste many URLs")
    sub.add_parser("themes", help="List available themes")
    sub.add_parser("update", help="Update yt-dlp now")
    sub.add_parser("doctor", help="Show current auth settings")
    sub.add_parser("cookies-help", help="Show cookie export instructions")
    sub.add_parser("cookies-clear", help="Delete stored cookies.txt")
    sub.add_parser("watch", help="Watch clipboard for YouTube URLs")
    sub.add_parser("stats", help="Show download statistics")
    p_search = sub.add_parser("search", help="Search YouTube and pick a video")
    p_search.add_argument("query", nargs="+", help="Search query")

    p_pl = sub.add_parser("playlist", help="Download a playlist URL")
    p_pl.add_argument("url")

    p_dir = sub.add_parser("set-dir", help="Change default download directory")
    p_dir.add_argument("path")

    p_theme = sub.add_parser("theme", help="Set theme")
    p_theme.add_argument("name")

    p_preset = sub.add_parser("preset", help="Apply / list / save / delete presets")
    p_preset.add_argument("args", nargs="+", metavar="ARG",
                          help="NAME | list | save NAME | delete NAME")

    p_get = sub.add_parser("get", help="Non-interactive download of URL(s)")
    p_get.add_argument("urls", nargs="+")
    p_get.add_argument("-o", "--output", metavar="DIR", help="download directory (this run only)")
    # SUPPRESS so these don't clobber the same flags given before the subcommand
    p_get.add_argument("-q", "--quality", choices=[k for k, _ in VIDEO_TARGETS],
                       default=argparse.SUPPRESS)
    p_get.add_argument("-a", "--audio", choices=[k for k, _, _ in AUDIO_FORMATS],
                       default=argparse.SUPPRESS)
    p_get.add_argument("-p", "--preset", default=argparse.SUPPRESS)
    p_get.add_argument("--sponsorblock", choices=list(SPONSOR_LEVELS), default=argparse.SUPPRESS,
                       help="cut SponsorBlock segments (this run only)")
    p_get.add_argument("--split-chapters", action="store_true", default=argparse.SUPPRESS,
                       help="also save one file per chapter (this run only)")

    p_sub = sub.add_parser("sub", help="Manage channel subscriptions")
    p_sub.add_argument("action", choices=["add", "list", "remove", "set"])
    p_sub.add_argument("target", nargs="?", help="channel URL / @handle, or # / name to remove or set")
    p_sub.add_argument("settings", nargs="*", metavar="KEY=VALUE",
                       help="for 'set':  audio=mp3  video=720  min=10  max=60  include=RE  exclude=RE  reset")
    p_sync = sub.add_parser("sync", help="Download new uploads from subscribed channels")
    p_sync.add_argument("--dry-run", action="store_true", help="only show what would be downloaded")
    p_retry = sub.add_parser("retry", help="Retry downloads that failed earlier")
    p_retry.add_argument("--list", action="store_true", help="only list the failures")
    p_retry.add_argument("--clear", action="store_true", help="forget all logged failures")
    sub.add_parser("tui", help="Full-screen terminal UI (needs Textual)")
    p_self = sub.add_parser("self-update", help="Update ytgrab itself from your source folder, a git repo or a zip")
    p_self.add_argument("source", nargs="?", help="folder or .zip (default: the folder ytgrab was installed from)")
    p_self.add_argument("--check", action="store_true", help="only report whether a newer version is available")
    p_self.add_argument("--preview", action="store_true", help="preview release notes, install plan and rollback safety without changing anything")
    p_self.add_argument("--rollback", action="store_true", help="reinstall the newest saved pre-update snapshot")
    p_self.add_argument("--demo-animation", action="store_true", help="preview the updater bucket animation; does not install anything")
    p_self.add_argument("--no-git", action="store_true", help="don't run git pull first")
    p_self.add_argument("--reinstall", action="store_true", help="reinstall even for an editable install")
    p_self.add_argument("-y", "--yes", action="store_true", help="don't ask before installing an older version")
    sub.add_parser("about", help="Show version, install method and source folder")
    p_ban = sub.add_parser("banner", help="Preview the banner styles")
    p_ban.add_argument("style", nargs="?", default="all",
                       choices=["large", "compact", "minimal", "all"])
    p_find = sub.add_parser("find", help="Search everything you downloaded, then open it")
    p_find.add_argument("words", nargs="+", help="words to look for (title, channel, file name)")
    p_find.add_argument("--audio", dest="only_audio", action="store_true", help="audio files only")
    p_find.add_argument("--video", dest="only_video", action="store_true", help="video files only")
    p_find.add_argument("-n", "--limit", dest="max_results", type=int, default=20, help="most results to show (default 20)")
    p_dup = sub.add_parser("dupes", help="Find duplicate files in your downloads and clean them up")
    p_dup.add_argument("--dry-run", action="store_true", help="only list what was found")
    p_dup.add_argument("--auto", action="store_true",
                       help="move verified identical copies to the trash without asking")
    p_dup.add_argument("--dir", metavar="DIR", help="folder to scan (default: your download folder)")
    p_bar = sub.add_parser("bar-style", help="Preview and choose the progress bar look")
    p_bar.add_argument("name", nargs="?", help="set it directly instead of picking")
    p_arch = sub.add_parser("archive", help="Show or clear the downloaded-videos archive")
    p_arch.add_argument("action", nargs="?", choices=["clear"])

    p_cookie = sub.add_parser("cookies-import", help="Import a Netscape cookies.txt")
    p_cookie.add_argument("path")

    p_pc = sub.add_parser("player-client", help="Set player client")
    p_pc.add_argument("client")

    p_batch = sub.add_parser("batch", help="Batch download from a file")
    p_batch.add_argument("file")

    p.add_argument("--sponsorblock", choices=list(SPONSOR_LEVELS), help="cut SponsorBlock segments (this run only)")
    p.add_argument("--split-chapters", action="store_true", help="also save one file per chapter (this run only)")
    p.add_argument("-V", "--version", action="version",
                   version=f"ytgrab {__version__}")
    p.add_argument("-p", "--preset", metavar="NAME", help="built-in preset or one of yours")
    p.add_argument("-l", "--limit", metavar="RATE")
    p.add_argument("-q", "--quality", choices=[k for k, _ in VIDEO_TARGETS])
    p.add_argument("-a", "--audio", choices=[k for k, _, _ in AUDIO_FORMATS])
    p.add_argument("-t", "--theme", choices=THEME_ORDER)
    p.add_argument("-f", "--file", metavar="PATH")

    return p.parse_args()


def _handle_input(cfg, url):
    """Process one line typed at the main prompt.
    Returns the (possibly reloaded) cfg, or None to quit."""
    if url in ("q", "quit", "exit", ""):
        print(paint("  Bye.", C.GREY)); return None
    if url in ("s", "settings"):
        return settings_menu(cfg)
    if url == "Q":
        queue_mode(cfg); return cfg
    if url in ("w", "watch"):
        clipboard_watch(cfg); return load_config()
    if url in ("t", "stats"):
        stats_dashboard(cfg)
        prompt("  Press enter to return… ")
        return cfg
    if url in ("p", "pending"):
        process_queue(cfg); return load_config()
    if url in ("f", "find"):
        words = prompt("  Find in your downloads  › ")
        if words:
            find_command(cfg, words)
            prompt("  Press enter to return… ")
        return cfg
    if url in ("d", "dupes"):
        dupes_command(cfg)
        prompt("  Press enter to return… ")
        return cfg

    if not (url.startswith("http://") or url.startswith("https://")):
        search_flow(cfg, url); return cfg

    if has_playlist_param(url):
        print()
        print(paint("  ⚠  This URL has a playlist parameter.", C.AMBER, C.BOLD))
        if not ask_yes_no("Download the whole playlist?", default=False):
            url = strip_playlist_param(url)
            print(paint("     ✓  Playlist param stripped.", C.GREEN))

    print()
    info = fetch_info(url, cfg)
    if info is None:
        prompt("  Press enter to continue… ")
        return cfg

    if info.get("_type") == "playlist":
        handle_playlist(cfg, info)
    else:
        download_one(cfg, url, info)
    return cfg


def _library_menu(cfg):
    rows = [(1, "Find a file", "search your history and download folder", "", False),
            (2, "Remove duplicates", "same video, same title, identical copies", "", False),
            (3, "Statistics", "what you downloaded, sizes, channels", "", False),
            (4, "History", "recent downloads", "", False),
            (5, "Archive", "videos remembered as downloaded", "", False)]
    print()
    pick = render_table("Library", rows, default_idx=1)
    if pick == 1:
        words = prompt("  Find in your downloads  › ")
        if words:
            find_command(cfg, words)
    elif pick == 2:
        dupes_command(cfg)
    elif pick == 3:
        stats_dashboard(cfg)
    elif pick == 4:
        show_history()
    else:
        cmd_archive("show")
    prompt("  Press enter to return… ")
    return cfg


def _update_menu(cfg):
    from . import selfupdate
    rows = [(1, "Check for updates", "compare with a folder or zip you point at", "", False),
            (2, "Update ytgrab", "install from a folder or zip, with automatic rollback", "", False),
            (3, "Preview the update animation", "installs nothing", "", False),
            (4, "Roll back", "return to the previous version", "", False),
            (5, "About", "version, install type, where things live", "", False)]
    print()
    pick = render_table("Update ytgrab", rows, default_idx=1)
    if pick in (1, 2):
        src = prompt("  Folder or .zip with the new ytgrab (Enter = the folder you installed from)  › ").strip()
        selfupdate.self_update(src or None, check=(pick == 1))
    elif pick == 3:
        selfupdate.demo_update_animation()
    elif pick == 4:
        selfupdate.rollback_install()
    else:
        selfupdate.about()
    prompt("  Press enter to return… ")
    return cfg


def _run_option(cfg, oid):
    """Run one dashboard option. Returns the (possibly reloaded) config."""
    if oid == "download":
        url = prompt("  Paste a video or playlist link  › ").strip()
        return _handle_input(cfg, url) if url else cfg
    if oid == "search":
        q = prompt("  Search YouTube for  › ").strip()
        if q:
            search_flow(cfg, q)
        return cfg
    if oid == "channels":
        from .channels import channels_menu
        channels_menu(cfg)
        return load_config()
    if oid == "stream":
        from .ytstream import interactive_loop
        interactive_loop(cfg)
        return load_config()
    if oid == "queue":
        process_queue(cfg)
        return load_config()
    if oid == "watch":
        clipboard_watch(cfg)
        return load_config()
    if oid == "library":
        return _library_menu(cfg)
    if oid == "settings":
        return settings_menu(cfg)
    if oid == "update":
        return _update_menu(cfg)
    return cfg


def _dashboard(cfg):
    """Draw banner + dashboard and wait for a choice. None when the terminal is not interactive."""
    import contextlib
    import io
    import shutil
    if not home.interactive():
        return None
    size = shutil.get_terminal_size((80, 24))
    budget = size.lines - (len(home.OPTIONS) + 12)          # rows the banner may use
    style = "large" if budget >= 8 else "compact" if budget >= 5 else "minimal"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        banner(cfg, size=style, clear=False, max_rows=max(8, budget))
    theme.clear_screen()
    sys.stdout.write(buf.getvalue())
    sys.stdout.flush()
    badges = {}
    pend = queue_count()
    if pend:
        badges["queue"] = f"{pend} waiting"
    try:
        from .channels import count as channel_count
        n = channel_count()
        if n:
            badges["channels"] = f"{n} saved"
    except Exception:
        pass
    import re
    plain = [re.sub(r"\033\[[0-9;]*m", "", ln) for ln in buf.getvalue().splitlines()]
    frame = [len(ln) for ln in plain if ln.startswith(("╭", "│", "╰"))]
    return home.run_home(cfg, badges, rows=max(6, size.lines - buf.getvalue().count("\n") - 1),
                         width=max(frame) if frame else None)


def main_loop(cfg):
    while True:
        try:
            choice = _dashboard(cfg)
        except KeyboardInterrupt:
            print(paint("  Bye.", C.GREY)); return
        if choice is not None:                       # the interactive dashboard
            kind, value = choice
            if kind == "quit":
                print(paint("  Bye.", C.GREY)); return
            try:
                cfg = (_run_option(cfg, value) if kind == "option" else _handle_input(cfg, value))
            except Back:                              # Ctrl+C inside a flow: back to the dashboard
                print(paint("  ↩  Cancelled.", C.GREY))
                continue
            if cfg is None:
                return
            continue
        # plain terminals and pipes keep the simple one-line prompt
        banner(cfg)
        pend = queue_count()
        if pend:
            print(paint(f"  📥  {pend} item(s) in pending queue   (press p to review)",
                        C.AMBER))
            print()
        try:
            url = prompt("  Paste URL or search  (q=quit, s=settings, Q=queue, w=watch, t=stats, p=pending, f=find, d=dupes)  › ")
        except Back:                      # Ctrl+C at the top level quits
            print(paint("  Bye.", C.GREY)); return
        try:
            cfg = _handle_input(cfg, url)
        except Back:                      # Ctrl+C inside a flow: back to the menu
            print(paint("  ↩  Cancelled.", C.GREY))
            continue
        if cfg is None:
            return


def main():
    try:
        _main()
    except (KeyboardInterrupt, Back):
        print(paint("\nBye.", C.GREY))


def _main():
    args = parse_args()
    if not (args.cmd == "self-update" and (args.preview or args.check or args.demo_animation)):
        harden_permissions()                # config dir 0700, cookies.txt 0600 (fixes older installs)
    cfg = load_config()
    apply_theme(cfg.get("theme", "youtube"))

    if getattr(args, "theme", None):
        cfg["theme"] = args.theme; apply_theme(args.theme)
    if getattr(args, "preset", None):
        if not apply_preset(cfg, args.preset, persist=False):     # -p is for this run only
            print(paint(f"  Unknown preset '{args.preset}'.  Try: ytgrab preset list", C.RED))
            sys.exit(2)
    if getattr(args, "quality", None):
        cfg["last_kind"] = "video"; cfg["last_video_quality"] = args.quality
    if getattr(args, "audio", None):
        cfg["last_kind"] = "audio"; cfg["last_audio_format"] = args.audio
    if getattr(args, "sponsorblock", None):
        cfg["sponsorblock"] = args.sponsorblock
    if getattr(args, "split_chapters", False):
        cfg["split_chapters"] = True
    if getattr(args, "limit", None):
        if not parse_rate(args.limit):
            print(paint(f"  Invalid rate '{args.limit}'. Use a number with a unit, e.g. 2M or 500K.", C.RED))
            sys.exit(2)
        cfg["speed_limit"] = args.limit

    if args.cmd == "watch":
        clipboard_watch(cfg); return
    if args.cmd == "stats":
        stats_dashboard(cfg); return
    if args.cmd == "search":
        search_flow(cfg, " ".join(args.query)); return
    if args.cmd == "playlist":
        info = fetch_info(args.url, cfg)
        if info and info.get("_type") == "playlist":
            handle_playlist(cfg, info)
        else:
            print(paint("  Not a playlist URL.", C.RED))
        return

    if args.cmd == "bar-style": sys.exit(cmd_bar_style(args.name))
    if args.cmd == "find":
        kind = "audio" if args.only_audio else "video" if args.only_video else None
        sys.exit(find_command(cfg, args.words, kind=kind, limit=args.max_results))
    if args.cmd == "dupes":
        sys.exit(dupes_command(cfg, dry_run=args.dry_run, auto=args.auto, directory=args.dir))
    if args.cmd == "history": show_history(); return
    if args.cmd == "stream":
        from .ytstream import run as stream_run
        sys.exit(stream_run(cfg, args.target, quality=args.quality))
    if args.cmd == "channels":
        from .channels import channels_menu
        channels_menu(cfg); return
    if args.cmd == "clear-history":
        save_history([]); print(paint("✓  History cleared.", C.GREEN)); return
    if args.cmd == "set-dir": cmd_set_dir(args.path); return
    if args.cmd == "theme": cmd_set_theme(args.name); return
    if args.cmd == "preset": cmd_set_preset(args.args); return
    if args.cmd == "archive": cmd_archive(args.action); return
    if args.cmd == "player-client": cmd_set_player_client(args.client); return
    if args.cmd == "cookies-import": cmd_cookies_import(args.path); return
    if args.cmd == "cookies-help": cookies_help(); return
    if args.cmd == "cookies-clear": cmd_cookies_clear(); return
    if args.cmd == "themes":
        print(paint("Available themes:", C.WHITE, C.BOLD))
        for t in THEME_ORDER:
            cur = "  ← current" if t == cfg.get("theme") else ""
            print(paint(f"  · {t}{cur}", C.OFF))
        return
    if args.cmd == "update":
        upgrade_ytdlp(); return
    if args.cmd == "doctor": cmd_doctor(cfg); return

    if args.cmd == "self-update":
        from .selfupdate import self_update
        sys.exit(self_update(args.source, check=args.check, preview=args.preview,
                             rollback=args.rollback, demo_animation=args.demo_animation,
                             no_git=args.no_git,
                             reinstall=args.reinstall, assume_yes=args.yes))
    Path(cfg["download_dir"]).mkdir(parents=True, exist_ok=True)
    if args.cmd == "about":
        from .selfupdate import about
        about(); return
    if args.cmd == "banner":
        styles = ["large", "compact", "minimal"] if args.style == "all" else [args.style]
        for st in styles:
            print(paint(f"  ── {st} ──", C.GREY_D))
            banner(cfg, size=st, clear=False)
        return
    if args.cmd == "tui":
        try:
            from .tui import run_tui
        except ImportError:
            print(paint("  The TUI needs Textual.  Install it with:", C.AMBER))
            print(paint("     pipx inject ytgrab textual      (or: pip install textual)", C.WHITE))
            sys.exit(1)
        run_tui(cfg); return
    if args.cmd == "get":
        if getattr(args, "output", None):
            cfg["download_dir"] = str(Path(args.output).expanduser())
            Path(cfg["download_dir"]).mkdir(parents=True, exist_ok=True)
        sys.exit(1 if get_command(cfg, args.urls) else 0)
    if args.cmd == "sync":
        sys.exit(1 if sync_subs(cfg, dry_run=args.dry_run) else 0)
    if args.cmd == "retry":
        sys.exit(retry_command(cfg, list_only=args.list, clear=args.clear))
    if args.cmd == "sub":
        if args.action == "list": cmd_sub_list(); return
        if not args.target:
            print(paint(f"  Usage: ytgrab sub {args.action} <channel>", C.RED)); sys.exit(2)
        if args.action == "set":
            ok = cmd_sub_set(args.target, args.settings)
        else:
            ok = cmd_sub_add(cfg, args.target) if args.action == "add" else cmd_sub_remove(args.target)
        sys.exit(0 if ok else 1)
    if args.cmd == "settings": settings_menu(cfg); return
    if args.cmd == "queue": queue_mode(cfg); return
    if args.cmd == "batch": batch_from_file(cfg, args.file); return
    if args.file: batch_from_file(cfg, args.file); return
    maybe_update_ytdlp(cfg)
    main_loop(cfg)


if __name__ == "__main__":
    main()
