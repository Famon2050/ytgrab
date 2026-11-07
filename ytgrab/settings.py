"""Settings menu and standalone CLI commands."""

import shutil
import time
from pathlib import Path

from .config import (
    COOKIES_FILE, CONFIG_DIR, DEFAULTS,
    atomic_write_text, load_config, load_history, save_config,
)
from .extras import clipboard_method, thumbnail_available
from .fmt import parse_rate, pretty_dir
from .theme import C, THEME_ORDER, W, apply_theme, banner, clear_screen, paint
from .progress import STYLES, STYLE_ORDER, preview_all
from .ui import ask_yes_no, prompt, render_table
from .ytdl import (
    AUDIO_FORMATS, BANNER_SIZES, BROWSER_OPTIONS, COOKIES_SOURCES,
    PLAYER_CLIENTS, SPONSOR_LABELS, VIDEO_TARGETS, sponsor_level,
    PRESETS, apply_preset, check_ytdlp_age, cookies_file_ok, delete_profile,
    save_profile,
)


def cookies_help():
    print()
    print(paint("  🍪  Exporting cookies for ytgrab", C.WHITE, C.BOLD))
    print(paint("  " + "─" * 50, C.BORDER)); print()
    print(paint("  YouTube blocks anonymous downloads with 403 errors.", C.OFF))
    print(paint("  Fix: export your YouTube cookies to a file once.", C.OFF)); print()
    print(paint("  Step 1.", C.AMBER, C.BOLD)
          + paint("  Install a cookies-export extension in your browser:", C.OFF))
    print(paint("            Firefox:  'Get cookies.txt LOCALLY'", C.GREY))
    print(paint("            Chrome:   'Get cookies.txt LOCALLY'", C.GREY)); print()
    print(paint("  Step 2.", C.AMBER, C.BOLD)
          + paint("  Log into youtube.com in that browser.", C.OFF)); print()
    print(paint("  Step 3.", C.AMBER, C.BOLD)
          + paint("  Click the extension → Export → save as cookies.txt", C.OFF)); print()
    print(paint("  Step 4.", C.AMBER, C.BOLD)
          + paint("  Import it into ytgrab:", C.OFF)); print()
    print(paint("            ytgrab cookies-import ~/Downloads/cookies.txt", C.WHITE, C.BOLD)); print()
    print(paint("  Cookies file location: ", C.GREY)
          + paint(str(COOKIES_FILE), C.OFF))
    exists = "✓ present" if cookies_file_ok() else "✗ not found"
    print(paint(f"  Status:                {exists}", C.GREEN if cookies_file_ok() else C.GREY_D))
    print()


def cmd_cookies_import(path):
    src = Path(path).expanduser()
    if not src.is_file():
        print(paint(f"  File not found: {src}", C.RED)); return
    text = src.read_text(encoding="utf-8", errors="ignore")
    if "youtube.com" not in text and "# Netscape HTTP Cookie File" not in text:
        print(paint("  ⚠  This doesn't look like a Netscape cookies.txt file.", C.AMBER))
        if not ask_yes_no("Import anyway?", default=False): return
    atomic_write_text(COOKIES_FILE, text, mode=0o600)     # session cookies: owner-only
    cfg = load_config()
    cfg["cookies_source"] = "file"
    save_config(cfg)
    print(paint(f"  ✓  Imported to {COOKIES_FILE}", C.GREEN))
    print(paint("     Cookies source set to 'file'.", C.GREY))


def cmd_cookies_clear():
    main_removed = COOKIES_FILE.exists()
    if main_removed:
        COOKIES_FILE.unlink()
        print(paint("  ✓  Cookies file removed.", C.GREEN))
    # install.sh keeps config backups; they contain a copy of cookies.txt too
    copies = 0
    for bk in CONFIG_DIR.parent.glob(CONFIG_DIR.name + ".bak-*"):
        try:
            (bk / "cookies.txt").unlink()
            copies += 1
        except OSError:
            pass
    if copies:
        print(paint(f"  ✓  Removed {copies} copy/copies from config backups too.", C.GREEN))
    if not main_removed and not copies:
        print(paint("  No cookies file to remove.", C.GREY))


def cmd_set_dir(path):
    cfg = load_config()
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        print(paint(f"Not a directory: {p}", C.RED)); return
    cfg["download_dir"] = str(p); save_config(cfg)
    print(paint(f"✓  Download dir set to {p}", C.GREEN))


def cmd_set_theme(name):
    cfg = load_config()
    from .theme import THEMES
    if name not in THEMES:
        print(paint(f"Unknown theme. Options: {', '.join(THEME_ORDER)}", C.RED)); return
    cfg["theme"] = name; save_config(cfg); apply_theme(name)
    print(paint(f"✓  Theme set to {name}", C.GREEN))


def choose_bar_style(cfg, preview=True):
    """Show every progress-bar style animating, then let the user pick one."""
    print()
    if preview:
        print(paint("  Progress bar styles  (live preview)", C.WHITE, C.BOLD))
        print()
        preview_all(current=cfg.get("bar_style", "classic"))
        print()
    cur = cfg.get("bar_style", "classic")
    rows = [(i, STYLES[n]["label"], STYLES[n]["desc"], "", False) for i, n in enumerate(STYLE_ORDER, 1)]
    idx = render_table("Pick a style", rows, default_idx=STYLE_ORDER.index(cur) + 1 if cur in STYLES else 1)
    cfg["bar_style"] = STYLE_ORDER[idx - 1]
    save_config(cfg)
    print(paint(f"  ✓  Bar style: {STYLES[cfg['bar_style']]['label']}", C.GREEN))
    return cfg["bar_style"]


def cmd_bar_style(name=None):
    """ytgrab bar-style [NAME]  — no name: preview all and pick; with a name: set it."""
    cfg = load_config()
    if name is None:
        choose_bar_style(cfg)
        return 0
    name = name.lower()
    if name not in STYLES:
        print(paint(f"Unknown style. Options: {', '.join(STYLE_ORDER)}", C.RED))
        return 2
    cfg["bar_style"] = name
    save_config(cfg)
    print(paint(f"✓  Bar style set to {STYLES[name]['label']}", C.GREEN))
    return 0


def cmd_set_preset(args):
    """ytgrab preset NAME | list | save NAME | delete NAME"""
    cfg = load_config()
    profiles = cfg.get("profiles") or {}
    if args[0] == "list":
        print(paint("Built-in:", C.WHITE, C.BOLD) + "  " + ", ".join(PRESETS))
        mine = ", ".join(profiles) or "(none yet — save one with: ytgrab preset save NAME)"
        print(paint("Yours:   ", C.WHITE, C.BOLD) + "  " + mine)
        return
    if args[0] in ("save", "delete"):
        if len(args) != 2:
            print(paint(f"Usage: ytgrab preset {args[0]} NAME", C.RED)); return
        if args[0] == "save":
            ok, msg = save_profile(cfg, args[1])
        else:
            ok = delete_profile(cfg, args[1])
            msg = f"Profile '{args[1]}' deleted." if ok else "No such profile."
        print(paint(("✓  " if ok else "✕  ") + msg, C.GREEN if ok else C.RED)); return
    name = args[0]
    if not apply_preset(cfg, name):
        print(paint("Unknown preset. Run: ytgrab preset list", C.RED)); return
    print(paint(f"✓  Preset '{name}' applied.", C.GREEN))


def cmd_set_player_client(client):
    cfg = load_config()
    client = client.lower()
    if client in ("auto", "default", ""):
        cfg["player_client"] = ""; save_config(cfg)
        print(paint("✓  Player client set to auto.", C.GREEN)); return
    valid = [c for c, _ in PLAYER_CLIENTS if c]
    if client not in valid:
        print(paint(f"Unknown client. Options: {', '.join(valid)}", C.RED)); return
    cfg["player_client"] = client; save_config(cfg)
    print(paint(f"✓  Player client set to {client}.", C.GREEN))


def cmd_doctor(cfg):
    print(paint("ytgrab doctor", C.WHITE, C.BOLD))
    print(paint("─" * 50, C.BORDER))
    print(f"  config:           {CONFIG_DIR / 'config.json'}")
    print(f"  download dir:     {cfg.get('download_dir')}")
    print(f"  cookies source:   {cfg.get('cookies_source')}")
    ok = cookies_file_ok()
    status = "✓ present" if ok else "✗ missing"
    print(f"  cookies file:     {COOKIES_FILE}  [{status}]")
    if cfg.get("cookies_source") == "browser":
        print(f"  cookies browser:  {cfg.get('cookies_from_browser') or '(not set)'}")
    print(f"  player client:    {cfg.get('player_client') or 'auto'}")
    print(f"  theme:            {cfg.get('theme')}")
    print(f"  thumbnail:        {'available' if thumbnail_available() else 'no viewer'}")
    from .library import archive_set
    from .subs import load as load_subs
    from .selfupdate import install_info
    ii = install_info()
    print(f"  ytgrab:           v{ii['version']}  ({'editable' if ii['editable'] else 'installed'}"
          f"{', pipx' if ii['pipx'] else ''})  source: {ii['source'] or 'unknown'}")
    print(f"  archive:          {len(archive_set())} video(s) recorded")
    print(f"  subscriptions:    {len(load_subs())}")
    from . import failed
    nf = failed.count()
    print(f"  failed downloads: {nf}" + ("   (ytgrab retry)" if nf else ""))
    print(f"  post hook:        {cfg.get('post_hook') or '(none)'}")
    print(f"  clipboard tool:   {clipboard_method() or '(none)'}")
    print(f"  ffmpeg:           {shutil.which('ffmpeg') or '✗ missing (needed for merging / audio)'}")
    js = next((n for n in ("deno", "node", "bun") if shutil.which(n)), None)
    print(f"  JS runtime:       {js or '(none found - recent yt-dlp may need deno for YouTube)'}")
    age, ver = check_ytdlp_age()
    print(f"  yt-dlp version:   {ver}" + (f"  ({age} days old)" if age is not None else ""))
    print()


def show_history():
    hist = load_history()
    clear_screen()
    print()
    if not hist:
        print(paint("  No history yet.", C.GREY)); print(); return
    print(paint(f"  Download history  ·  {len(hist)} entries", C.WHITE, C.BOLD))
    print(paint("  " + "─" * (W - 4), C.BORDER)); print()
    for e in reversed(hist):
        title = e.get("title", "?")
        if len(title) > 46: title = title[:45] + "…"
        print(paint("  ●  ", C.RED) + paint(f"{e.get('date', '')}  ", C.GREY_D)
              + paint(f"{e.get('kind', '?'):<5} ", C.AMBER)
              + paint(f"{e.get('quality', ''):<6} ", C.GREY)
              + paint(title, C.OFF))
    print()


def settings_menu(cfg):
    while True:
        banner(cfg)
        print(paint("  ⚙  Settings", C.WHITE, C.BOLD))
        print()

        sub_state = "on " if cfg["subtitles_enabled"] else "off"
        clip_state = "on " if cfg["clip_enabled"] else "off"
        _lvl = sponsor_level(cfg)
        sb_state = "off" if _lvl == "off" else next(lb for k, lb, _ in SPONSOR_LABELS if k == _lvl).lower()
        nt_state = "on " if cfg.get("notify", True) else "off"
        src = cfg.get("cookies_source", "file")
        if src == "file":
            cookies_label = "file  (" + ("✓ found" if cookies_file_ok() else "✗ missing") + ")"
        elif src == "browser":
            cookies_label = "browser: " + (cfg.get("cookies_from_browser") or "not set")
        else:
            cookies_label = "disabled"

        rows = [
            ("1",  "Download directory",          pretty_dir(cfg["download_dir"])),
            ("2",  "Theme",                       cfg.get("theme", "youtube")),
            ("3",  "Banner size",                 cfg["banner_size"]),
            ("4",  "Default type",                cfg["last_kind"]),
            ("5",  "Default video quality",       cfg["last_video_quality"]),
            ("6",  "Default audio format",        cfg["last_audio_format"]),
            ("7",  "Subtitles feature",           sub_state),
            ("8",  "Subtitle language",           cfg["last_sub_lang"]),
            ("9",  "Clip-range feature",          clip_state),
            ("10", "SponsorBlock (skip segments)", sb_state),
            ("11", "Speed limit",                 cfg.get("speed_limit") or "none"),
            ("12", "Desktop notifications",       nt_state),
            ("13", "yt-dlp age check on start",   "on " if cfg.get("check_ytdlp", True) else "off"),
            ("14", "Cookies source",              cookies_label),
            ("15", "Player client",               cfg.get("player_client") or "auto"),
            ("16", "Max concurrent downloads",    f"{cfg.get('max_concurrent', 3)} at a time"),
            ("17", "Skip already-downloaded (archive)", "on " if cfg.get("use_archive", True) else "off"),
            ("18", "Playlist folders",            "on " if cfg.get("playlist_folders", True) else "off"),
            ("19", "Embed subtitles into video",  "on " if cfg.get("embed_subs", True) else "off"),
            ("20", "Embed metadata + chapters",   "on " if cfg.get("embed_metadata", True) else "off"),
            ("21", "Retries per download",        str(cfg.get("retries", 10))),
            ("22", "Post-download hook",          cfg.get("post_hook") or "none"),
            ("23", "Thumbnail preview",           "on " if cfg.get("thumb_preview") else "off"),
            ("24", "Reset preferences",           ""),
            ("25", "Prefer h264/AAC (plays everywhere)", "on " if cfg.get("prefer_compat", True) else "off"),
            ("26", "Also split into chapter files", "on " if cfg.get("split_chapters") else "off"),
            ("27", "Progress bar style",          STYLES.get(cfg.get("bar_style", "classic"), STYLES["classic"])["label"]),
            ("28", "Speed + ETA on the progress bar", "on " if cfg.get("show_speed_eta", True) else "off"),
            ("29", "Video container (MP4 / MKV)", cfg.get("video_container", "mp4").upper()),
            ("0",  "Back",                        ""),
        ]
        for num, label, val in rows:
            n = paint(f"  {num:>2}.", C.RED, C.BOLD)
            l = paint(f"{label:<34}", C.OFF)
            if val in ("on ", "off"):
                v = paint(val, C.GREEN if val == "on " else C.GREY_D, C.BOLD)
            else:
                v = paint(str(val), C.WHITE, C.BOLD) if val else ""
            print(f"{n}  {l}{v}")

        print()
        print(paint("  Tip: 403 errors → run 'ytgrab cookies-help'", C.GREY_D))
        print()
        choice = prompt("  Choose  › ").strip()

        if choice in ("0", ""):
            save_config(cfg); return cfg

        if choice == "1":
            print()
            print(paint(f"  Current: {cfg['download_dir']}", C.GREY))
            new = prompt("  New path (Enter to keep)  › ")
            if new:
                p = Path(new).expanduser().resolve()
                if p.is_dir():
                    cfg["download_dir"] = str(p); save_config(cfg)
                    print(paint(f"  ✓  Set to {p}", C.GREEN))
                else:
                    print(paint(f"  ✕  Not a directory: {p}", C.RED))
                time.sleep(1)

        elif choice == "2":
            print()
            rows = [(i, name, "", "", False) for i, name in enumerate(THEME_ORDER, 1)]
            default_idx = THEME_ORDER.index(cfg.get("theme", "youtube")) + 1
            idx = render_table("Theme", rows, default_idx=default_idx)
            cfg["theme"] = THEME_ORDER[idx - 1]
            apply_theme(cfg["theme"]); save_config(cfg)

        elif choice == "3":
            print()
            rows = [(i, label, "", "", False) for i, (_, label) in enumerate(BANNER_SIZES, 1)]
            default_idx = next((i for i, (k, _) in enumerate(BANNER_SIZES, 1)
                                if k == cfg["banner_size"]), 1)
            idx = render_table("Banner size", rows, default_idx=default_idx)
            cfg["banner_size"] = BANNER_SIZES[idx - 1][0]; save_config(cfg)

        elif choice == "4":
            print()
            rows = [(1, "Video", "MP4", "", False), (2, "Audio only", "", "", False)]
            idx = render_table("Default type", rows,
                               default_idx=1 if cfg["last_kind"] == "video" else 2)
            cfg["last_kind"] = "video" if idx == 1 else "audio"; save_config(cfg)

        elif choice == "5":
            print()
            rows = [(i, label, "", "", False) for i, (_, label) in enumerate(VIDEO_TARGETS, 1)]
            default_idx = next((i for i, (k, _) in enumerate(VIDEO_TARGETS, 1)
                                if k == cfg["last_video_quality"]), 1)
            idx = render_table("Default video quality", rows, default_idx=default_idx)
            cfg["last_video_quality"] = VIDEO_TARGETS[idx - 1][0]; save_config(cfg)

        elif choice == "6":
            print()
            rows = [(i, label, detail, "", False)
                    for i, (_, label, detail) in enumerate(AUDIO_FORMATS, 1)]
            default_idx = next((i for i, (k, _, _) in enumerate(AUDIO_FORMATS, 1)
                                if k == cfg["last_audio_format"]), 1)
            idx = render_table("Default audio format", rows, default_idx=default_idx)
            cfg["last_audio_format"] = AUDIO_FORMATS[idx - 1][0]; save_config(cfg)

        elif choice == "7":
            print()
            cfg["subtitles_enabled"] = ask_yes_no("Enable subtitles feature?",
                                                  default=cfg["subtitles_enabled"])
            save_config(cfg)

        elif choice == "8":
            print()
            new = prompt(f"  Subtitle language code  [{cfg['last_sub_lang']}]  › ")
            if new: cfg["last_sub_lang"] = new; save_config(cfg)

        elif choice == "9":
            print()
            cfg["clip_enabled"] = ask_yes_no("Enable clip-range feature?",
                                             default=cfg["clip_enabled"])
            save_config(cfg)

        elif choice == "10":
            print()
            rows = [(i, label, detail, "", False) for i, (_, label, detail) in enumerate(SPONSOR_LABELS, 1)]
            cur = sponsor_level(cfg)
            default_idx = next((i for i, (k, _, _) in enumerate(SPONSOR_LABELS, 1) if k == cur), 1)
            idx = render_table("SponsorBlock: cut these segments out", rows, default_idx=default_idx)
            cfg["sponsorblock"] = SPONSOR_LABELS[idx - 1][0]
            save_config(cfg)

        elif choice == "11":
            print()
            print(paint(f"  Current: {cfg.get('speed_limit') or 'none'}", C.GREY))
            print(paint("  Examples:  2M  = 2 MB/s   ·   500K  = 500 KB/s   ·   empty = no limit", C.GREY_D))
            new = prompt("  New limit  › ")
            if new == "":
                cfg["speed_limit"] = ""
                save_config(cfg)
                print(paint("  ✓  Speed limit cleared.", C.GREEN))
            elif parse_rate(new):
                cfg["speed_limit"] = new; save_config(cfg)
                print(paint(f"  ✓  Limit set to {new}/s", C.GREEN))
            else:
                print(paint("  ✕  Invalid rate. Use a number with a unit, e.g. 2M or 500K.", C.RED))
            time.sleep(1)

        elif choice == "12":
            print()
            cfg["notify"] = ask_yes_no("Desktop notifications on finish?", default=cfg.get("notify", True))
            save_config(cfg)

        elif choice == "13":
            print()
            cfg["check_ytdlp"] = ask_yes_no("Check yt-dlp age on startup?", default=cfg.get("check_ytdlp", True))
            save_config(cfg)

        elif choice == "14":
            print()
            rows = [(i, label, "", "", False) for i, (_, label) in enumerate(COOKIES_SOURCES, 1)]
            default_idx = next((i for i, (k, _) in enumerate(COOKIES_SOURCES, 1)
                                if k == cfg.get("cookies_source", "file")), 1)
            idx = render_table("Cookies source", rows, default_idx=default_idx)
            chosen = COOKIES_SOURCES[idx - 1][0]
            cfg["cookies_source"] = chosen
            if chosen == "browser":
                print()
                brows = [(i, label, "", "", False)
                         for i, (_, label) in enumerate(BROWSER_OPTIONS, 1)]
                di = next((i for i, (k, _) in enumerate(BROWSER_OPTIONS, 1)
                           if k == cfg.get("cookies_from_browser", "")), 1)
                bi = render_table("Which browser?", brows, default_idx=di)
                cfg["cookies_from_browser"] = BROWSER_OPTIONS[bi - 1][0]
            save_config(cfg)
            if chosen == "file" and not cookies_file_ok():
                print()
                print(paint("  No cookies.txt found yet.", C.AMBER))
                if ask_yes_no("Show export instructions?", default=True):
                    cookies_help()
                    prompt("  Press enter to return… ")
            elif chosen == "file":
                print(paint(f"  ✓  Using {COOKIES_FILE}", C.GREEN))
                time.sleep(1)

        elif choice == "15":
            print()
            rows = [(i, label, "", "", False)
                    for i, (_, label) in enumerate(PLAYER_CLIENTS, 1)]
            default_idx = next((i for i, (k, _) in enumerate(PLAYER_CLIENTS, 1)
                                if k == cfg.get("player_client", "")), 1)
            idx = render_table("Player client", rows, default_idx=default_idx)
            cfg["player_client"] = PLAYER_CLIENTS[idx - 1][0]; save_config(cfg)

        elif choice == "16":
            print()
            rows = [(i, f"{i} at a time", "", "", False) for i in (1, 2, 3, 4)]
            di = int(cfg.get("max_concurrent", 3) or 3)
            di = max(1, min(4, di))
            idx = render_table("Max concurrent downloads", rows, default_idx=di)
            cfg["max_concurrent"] = idx
            save_config(cfg)
            print(paint(f"  ✓  Set to {idx} concurrent download(s).", C.GREEN))
            time.sleep(1)

        elif choice == "17":
            print()
            cfg["use_archive"] = ask_yes_no(
                "Skip videos you already downloaded (playlists / batches / sync)?",
                default=cfg.get("use_archive", True))
            save_config(cfg)

        elif choice == "18":
            print()
            cfg["playlist_folders"] = ask_yes_no(
                "Put each playlist in its own numbered folder?",
                default=cfg.get("playlist_folders", True))
            save_config(cfg)

        elif choice == "19":
            print()
            cfg["embed_subs"] = ask_yes_no(
                "Embed subtitles in the video (no loose .srt files)?",
                default=cfg.get("embed_subs", True))
            save_config(cfg)

        elif choice == "20":
            print()
            cfg["embed_metadata"] = ask_yes_no(
                "Embed title / uploader / chapters in the file?",
                default=cfg.get("embed_metadata", True))
            save_config(cfg)

        elif choice == "21":
            print()
            new = prompt(f"  Retries  [{cfg.get('retries', 10)}]  (0-50)  › ")
            if new.isdigit() and 0 <= int(new) <= 50:
                cfg["retries"] = int(new); save_config(cfg)
            elif new:
                print(paint("  ✕  Enter a number from 0 to 50.", C.RED)); time.sleep(1)

        elif choice == "22":
            print()
            print(paint(f"  Current: {cfg.get('post_hook') or 'none'}", C.GREY))
            print(paint("  Runs after each download, WITHOUT a shell. {file} = saved file.", C.GREY_D))
            print(paint("  Env vars: YTGRAB_FILE  YTGRAB_TITLE  YTGRAB_URL  YTGRAB_KIND", C.GREY_D))
            print(paint("  Example:  mv {file} /media/music      ·  '-' clears it", C.GREY_D))
            new = prompt("  Command  › ")
            if new == "-":
                cfg["post_hook"] = ""; save_config(cfg)
            elif new:
                cfg["post_hook"] = new; save_config(cfg)

        elif choice == "23":
            print()
            cfg["thumb_preview"] = ask_yes_no(
                "Show the thumbnail on the info card? (needs chafa / timg / viu)",
                default=cfg.get("thumb_preview", False))
            save_config(cfg)

        elif choice == "25":
            print()
            cfg["prefer_compat"] = ask_yes_no(
                "Prefer h264 + AAC when the resolution is the same (MP4 plays in QuickTime, TVs, editors)?",
                default=cfg.get("prefer_compat", True))
            save_config(cfg)

        elif choice == "26":
            print()
            print(paint("  Videos with chapters also get one file per chapter, in a folder named after the video.", C.GREY_D))
            print(paint("  The full video is kept too.", C.GREY_D))
            cfg["split_chapters"] = ask_yes_no("Split videos into chapter files?",
                                               default=bool(cfg.get("split_chapters")))
            save_config(cfg)

        elif choice == "27":
            choose_bar_style(cfg)

        elif choice == "28":
            print()
            cfg["show_speed_eta"] = ask_yes_no("Show download speed and ETA next to the bar?",
                                               default=cfg.get("show_speed_eta", True))
            save_config(cfg)

        elif choice == "29":
            print()
            rows = [(1, "MP4", "Broad device compatibility", "", False),
                    (2, "MKV", "Flexible container; preserves modern codecs", "", False)]
            current = str(cfg.get("video_container", "mp4")).lower()
            idx = render_table("Video container", rows, default_idx=2 if current == "mkv" else 1)
            cfg["video_container"] = "mkv" if idx == 2 else "mp4"
            save_config(cfg)

        elif choice == "24":
            print()
            if ask_yes_no("Reset all preferences to defaults?", default=False):
                keep_dir, keep_profiles = cfg["download_dir"], cfg.get("profiles") or {}
                cfg.clear(); cfg.update(DEFAULTS)
                cfg["download_dir"] = keep_dir
                cfg["profiles"] = dict(keep_profiles)
                save_config(cfg); apply_theme(cfg["theme"])
                print(paint("  ✓  Preferences reset.", C.GREEN)); time.sleep(1)
