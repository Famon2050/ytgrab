"""Full-screen terminal UI (needs Textual):   ytgrab tui

Downloads run on the same engine as the CLI (JobRunner) and share its
archive, history, hooks, subscriptions and settings.
"""

import functools
import time
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import (
    Button, DataTable, Footer, Header, Input, Label, Select, SelectionList,
    Static, TabbedContent, TabPane,
)
from textual.widgets.selection_list import Selection

from . import pending, subs
from . import __version__
from .config import load_history, save_config
from .extras import get_entries
from .flows import _record_done, _record_failed
from .fmt import fmt_duration, fmt_size, fmt_views_short, parse_length_filter
from .library import DupIndex, archive_add, archive_key, archive_set, find_existing, key_for_entry
from .multi import Job, JobRunner
from .search import filter_results, search_youtube
from .theme import THEME_ORDER
from .ytdl import (
    AUDIO_FORMATS, VIDEO_TARGETS, build_audio_opts, build_video_opts,
    channel_template, fetch_info_quiet, has_playlist_param, open_path,
    playlist_template, strip_playlist_param,
)

# ytgrab theme name -> built-in Textual theme
TEXTUAL_THEMES = {
    "youtube": "textual-dark", "nord": "nord", "dracula": "dracula",
    "gruvbox": "gruvbox", "catppuccin": "catppuccin-mocha",
}

_STATUS = {
    "queued": ("⏳ queued", "grey62"), "downloading": ("⬇ downloading", "cyan"),
    "paused": ("⏸ paused", "yellow"), "processing": ("⚙ processing", "green"),
    "done": ("✓ done", "bold green"), "error": ("✗ error", "bold red"),
    "cancelled": ("⊘ cancelled", "yellow"),
}
_FINISHED = ("done", "error", "cancelled")
_BAR_W = 12


def _bar(pct):
    pct = max(0, min(100, int(pct)))
    filled = _BAR_W * pct // 100
    return "█" * filled + "░" * (_BAR_W - filled) + f" {pct:>3}%"


def _guard(fn):
    """UI callbacks (timers, worker results) can fire while the app is shutting down,
    after its widgets are gone. Make them do nothing instead of raising."""
    @functools.wraps(fn)
    def wrapper(self, *a, **k):
        if not self.is_running:
            return None
        try:
            return fn(self, *a, **k)
        except NoMatches:
            return None
    return wrapper


def make_job(cfg, kind, quality, url, title, key="", channel="", outtmpl=None):
    """Build a Job with the options the CLI would use for the same choice."""
    out = Path(cfg["download_dir"])
    if kind == "video":
        opts = build_video_opts(
            quality, out, cfg, subtitles=bool(cfg.get("subtitles_enabled")),
            sub_lang=cfg.get("last_sub_lang", "en"), outtmpl=outtmpl)
    else:
        opts = build_audio_opts(quality, out, cfg, outtmpl=outtmpl)
    return Job(idx=0, url=url, title=title or url, kind=kind, quality=quality,
               opts=opts, channel=channel, archive_key=key or "")


# ----------------------------------------------------------------------
# Modal dialogs
# ----------------------------------------------------------------------
class ChoiceScreen(ModalScreen):
    """Message + buttons. Dismisses with the chosen button's id ('cancel' on Esc)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, message, choices):
        super().__init__()
        self._message = message
        self._choices = choices          # [(id, label, variant)]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(Text(self._message), id="dialog-msg")
            with Horizontal(id="dialog-buttons"):
                for cid, label, variant in self._choices:
                    yield Button(label, id=f"c-{cid}", variant=variant)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id[2:])

    def action_cancel(self) -> None:
        self.dismiss("cancel")


class ChannelAddScreen(ModalScreen):
    """Collect a YouTube channel URL or creator handle."""
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(Text("Add a creator to your saved channel list"), id="dialog-msg")
            yield Label(Text("Paste a YouTube channel URL or type an @handle."))
            yield Input(placeholder="https://youtube.com/@creator or @creator", id="channel-input")
            with Horizontal(id="dialog-buttons"):
                yield Button("Continue", id="channel-save", variant="success")
                yield Button("Cancel", id="channel-cancel", variant="error")

    def on_mount(self) -> None:
        self.query_one("#channel-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "channel-save":
            self.dismiss(self.query_one("#channel-input", Input).value.strip())
        else:
            self.dismiss(None)

    @on(Input.Submitted, "#channel-input")
    def _submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class PlaylistScreen(ModalScreen):
    """Pick videos from a playlist. Dismisses with a list of indexes, or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title, entries, skipped):
        super().__init__()
        self._title, self._entries, self._skipped = title, entries, skipped

    def compose(self) -> ComposeResult:
        sels = []
        for i, e in enumerate(self._entries):
            dur = fmt_duration(e["duration"]) if e.get("duration") else "?"
            label = f"{i + 1:>3}. {e['title'][:62]}  [{dur}]"
            if i in self._skipped:
                label += "  (already downloaded)"
            sels.append(Selection(Text(label), i, i not in self._skipped,
                                  disabled=i in self._skipped))
        with Vertical(id="dialog"):
            yield Label(Text(f"{self._title}  ·  {len(self._entries)} videos"), id="dialog-msg")
            yield SelectionList(*sels, id="plsel")
            with Horizontal(id="dialog-buttons"):
                yield Button("Download selected", id="pl-ok", variant="success")
                yield Button("Select all", id="pl-all")
                yield Button("None", id="pl-none")
                yield Button("Cancel", id="pl-cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        sl = self.query_one("#plsel", SelectionList)
        bid = event.button.id
        if bid == "pl-ok":
            self.dismiss(sorted(v for v in sl.selected if v not in self._skipped))
        elif bid == "pl-all":
            sl.select_all()
            for value in self._skipped:
                sl.deselect(value)
        elif bid == "pl-none":
            sl.deselect_all()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ChannelExploreScreen(PlaylistScreen):
    """Browse a creator's newest videos and queue any selected uploads."""


# ----------------------------------------------------------------------
# The app
# ----------------------------------------------------------------------
class YtGrabApp(App):
    TITLE = "ytgrab"

    CSS = """
    Screen { overflow: hidden; }
    #tabs { height: 1fr; }
    #home-wrap { height: 1fr; padding: 1 2; }
    #home-hero { height: 5; padding: 1 2; border: round $primary; background: $surface; color: $text; }
    #home-actions { height: 3; margin: 1 0; }
    #sub-actions { height: 3; margin: 0 1; }
    #sub-actions Button { margin-right: 1; }
    #home-actions Button { margin-right: 1; }
    #home-panels { height: 1fr; }
    .home-panel { width: 1fr; height: 1fr; border: round $secondary; padding: 0 1; margin-right: 1; }
    .home-panel-title { height: 1; color: $primary; text-style: bold; }
    #home-channels, #home-recent { height: 1fr; }
    TabPane { height: 1fr; padding: 0; }
    #bar { height: 3; }
    #url { width: 1fr; }
    #kind { width: 16; }
    #quality { width: 24; }
    #container { width: 12; }
    #lenfilter { height: 3; }
    DataTable { height: 1fr; }
    .hint { color: $text-muted; height: 1; padding: 0 1; }
    #hist-summary { height: 1; padding: 0 1; color: $text-muted; }
    ChoiceScreen, PlaylistScreen { align: center middle; }
    #dialog { width: 84; max-width: 95%; height: auto; max-height: 92%;
              border: thick $primary; background: $surface; padding: 1 2; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: center; }
    #dialog-buttons Button { margin: 0 1; }
    #plsel { height: 16; margin: 1 0; }
    """

    BINDINGS = [
        Binding("slash", "focus_input", "Add/search"),
        Binding("escape", "leave_input", "Shortcuts", show=False),
        Binding("p", "toggle_pause", "Pause"),
        Binding("c", "cancel_job", "Cancel"),
        Binding("r", "retry", "Retry"),
        Binding("x", "clear_done", "Clear"),
        Binding("o", "open_file", "Open"),
        Binding("m", "toggle_kind", "Vid/Aud"),
        Binding("s", "sync", "Sync"),
        Binding("t", "cycle_theme", "Theme"),
        Binding("space", "mark_result", "Mark", show=False),
        Binding("a", "mark_all", "Mark all", show=False),
        Binding("delete", "delete_row", "Remove", show=False),
        Binding("h", "tab('tab-home')", "Home"),
        Binding("1", "tab('tab-dl')", "Downloads", show=False),
        Binding("2", "tab('tab-search')", "Search", show=False),
        Binding("3", "tab('tab-pending')", "Pending", show=False),
        Binding("4", "tab('tab-subs')", "Subs", show=False),
        Binding("5", "tab('tab-hist')", "History", show=False),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.jobs = []
        self._rows = {}             # id(job) -> row key
        self._by_row = {}           # row key -> job
        self._shown = {}            # id(job) -> last rendered tuple
        self._next_id = 1
        self._results, self._query = [], ""
        self._lo = self._hi = None
        self._shown_results = []
        self._marked = set()        # urls of the search results ticked with Space
        self._syncing_select = False
        self.runner = JobRunner(
            workers=int(cfg.get("max_concurrent", 3) or 3),
            on_finish=self._thread_finished, quiet=True)

    # ---------------- layout ----------------
    def _quality_options(self, kind):
        if kind == "video":
            return [(label, key) for key, label in VIDEO_TARGETS]
        return [(f"{label}  ·  {detail}", key) for key, label, detail in AUDIO_FORMATS]

    def _default_quality(self, kind):
        key = "last_video_quality" if kind == "video" else "last_audio_format"
        val = self.cfg.get(key)
        valid = [v for _, v in self._quality_options(kind)]
        return val if val in valid else valid[0 if kind == "audio" else 1]

    def compose(self) -> ComposeResult:
        kind = self.cfg.get("last_kind", "video")
        kind = kind if kind in ("video", "audio") else "video"
        yield Header()
        with Horizontal(id="bar"):
            yield Input(placeholder="Paste a video / playlist / channel URL — or type a search…",
                        id="url")
            yield Select([("Video", "video"), ("Audio", "audio")], value=kind,
                         allow_blank=False, id="kind")
            yield Select(self._quality_options(kind), value=self._default_quality(kind),
                         allow_blank=False, id="quality")
            yield Select([("MP4", "mp4"), ("MKV", "mkv")],
                         value=self.cfg.get("video_container", "mp4") if self.cfg.get("video_container") in ("mp4", "mkv") else "mp4",
                         allow_blank=False, id="container")
        with TabbedContent(id="tabs", initial="tab-home"):
            with TabPane("Home", id="tab-home"):
                with Vertical(id="home-wrap"):
                    yield Static(id="home-hero")
                    with Horizontal(id="home-actions"):
                        yield Button("↻ Check creator updates", id="home-sync", variant="primary")
                        yield Button("＋ Add a creator", id="home-add", variant="success")
                        yield Button("⌕ Search YouTube", id="home-search")
                        yield Button("↓ Downloads", id="home-downloads")
                    with Horizontal(id="home-panels"):
                        with Vertical(classes="home-panel"):
                            yield Static("SAVED CREATORS · Enter checks one", classes="home-panel-title")
                            yield DataTable(id="home-channels", cursor_type="row", zebra_stripes=True)
                        with Vertical(classes="home-panel"):
                            yield Static("RECENT DOWNLOADS", classes="home-panel-title")
                            yield DataTable(id="home-recent", cursor_type="row", zebra_stripes=True)
            with TabPane("Downloads", id="tab-dl"):
                yield DataTable(id="dl", cursor_type="row", zebra_stripes=True)
            with TabPane("Search", id="tab-search"):
                yield Input(placeholder="Length filter, minutes:  10-60   >20   <5   (Enter; empty clears)",
                            id="lenfilter")
                yield Static("Space = mark a result · a = mark all · Enter = download the marked "
                             "(or the highlighted one)", classes="hint")
                yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
            with TabPane("Pending", id="tab-pending"):
                yield Static("Enter = download · Delete = remove · filled by `ytgrab watch`",
                             classes="hint")
                yield DataTable(id="pend", cursor_type="row", zebra_stripes=True)
            with TabPane("Subscribers", id="tab-subs"):
                with Horizontal(id="sub-actions"):
                    yield Button("＋ Add", id="sub-add", variant="success")
                    yield Button("− Remove", id="sub-remove", variant="error")
                    yield Button("⌕ Explore", id="sub-explore", variant="primary")
                    yield Button("↻ Check one", id="sub-check")
                    yield Button("↻ Check all", id="sub-check-all")
                yield Static("Saved YouTube channels and creators · Enter or Explore opens recent uploads · Check selected/all fetches new uploads", classes="hint")
                yield DataTable(id="subs", cursor_type="row", zebra_stripes=True)
            with TabPane("History", id="tab-hist"):
                yield Static(id="hist-summary")
                yield DataTable(id="hist", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f"v{__version__}"
        t = self.query_one("#dl", DataTable)
        for key, label, width in (("num", "#", 3), ("title", "Title", 38), ("kind", "Type", 6),
                                  ("status", "Status", 14), ("prog", "Progress", 18),
                                  ("speed", "Speed", 11), ("eta", "ETA", 7), ("size", "Size", 9),
                                  ("quality", "Quality / file", 15)):
            t.add_column(label, key=key, width=width)
        r = self.query_one("#results", DataTable)
        for label, width in (("✓", 2), ("#", 3), ("Title", 44), ("Time", 8), ("Channel", 18), ("Views", 6)):
            r.add_column(label, width=width)
        p = self.query_one("#pend", DataTable)
        for label, width in (("#", 3), ("Title", 60), ("Added", 17)):
            p.add_column(label, width=width)
        s = self.query_one("#subs", DataTable)
        for label, width in (("Channel", 30), ("Last checked", 18), ("Channel URL", 34)):
            s.add_column(label, width=width)
        h = self.query_one("#hist", DataTable)
        for label, width in (("Date", 16), ("Type", 6), ("Quality", 8), ("Size", 9),
                             ("Channel", 20), ("Title", 50)):
            h.add_column(label, width=width)
        home_channels = self.query_one("#home-channels", DataTable)
        home_channels.add_column("Creator", key="creator", width=28)
        home_channels.add_column("Last check", key="checked", width=20)
        home_channels.add_column("Updates", key="updates", width=10)
        home_recent = self.query_one("#home-recent", DataTable)
        home_recent.add_column("Downloaded", key="title", width=36)
        home_recent.add_column("When", key="when", width=18)
        self._apply_theme(self.cfg.get("theme", "youtube"))
        self._refresh_home()
        self._refresh_pending()
        self._refresh_subs()
        self._tick_timer = self.set_interval(0.3, self._tick)
        self.query_one("#url", Input).focus()

    @_guard
    def _update_home_hero(self):
        saved_count = len(subs.load())
        completed = sum(1 for j in self.jobs if j.status == "done")
        active = sum(1 for j in self.jobs if j.status in ("downloading", "processing"))
        queued_jobs = sum(1 for j in self.jobs if j.status in ("queued", "paused"))
        waiting = queued_jobs + len(pending.all_items())
        self._home_counts = (active, queued_jobs, completed, waiting)
        if saved_count:
            message = (f"ytgrab  v{__version__}  ·  Your creator feed\n"
                       f"{saved_count} saved creator(s)  ·  {active} active  ·  {queued_jobs} queued  ·  "
                       f"{completed} completed this session  ·  {waiting} waiting total\n"
                       "Press Enter on a saved creator to check its latest uploads; use Sync to check everyone.")
        else:
            message = (f"ytgrab  v{__version__}  ·  Your creator feed\n"
                       "No creators saved yet. Add a YouTube channel URL or @handle to follow new uploads.\n"
                       f"{active} active downloads  ·  {queued_jobs} queued  ·  {completed} completed  ·  {waiting} waiting total")
        self.query_one("#home-hero", Static).update(message)

    @_guard
    def _refresh_home(self):
        hist = load_history()
        self._update_home_hero()
        table = self.query_one("#home-channels", DataTable)
        table.clear()
        for i, sub in enumerate(subs.load()):
            table.add_row(Text(sub.get("title", "Creator")),
                          Text(sub.get("last_sync") or "Not checked yet"),
                          Text("Enter ↵"), key=str(i))
        recent = self.query_one("#home-recent", DataTable)
        recent.clear()
        for entry in list(reversed(hist))[:8]:
            recent.add_row(Text(str(entry.get("title") or "Untitled")[:80]),
                           Text(str(entry.get("date") or "")))

    @on(Button.Pressed, "#home-sync")
    def _home_sync_pressed(self, event: Button.Pressed) -> None:
        self.action_sync()

    @on(Button.Pressed, "#home-add")
    def _home_add_pressed(self, event: Button.Pressed) -> None:
        self.query_one(TabbedContent).active = "tab-subs"
        url = self.query_one("#url", Input)
        url.focus()
        self.notify("Paste a YouTube channel URL or @handle above, then press Enter.", timeout=5)

    @on(Button.Pressed, "#home-search")
    def _home_search_pressed(self, event: Button.Pressed) -> None:
        self.query_one(TabbedContent).active = "tab-search"
        self.query_one("#url", Input).focus()
        self.notify("Type a search query in the top bar and press Enter.", timeout=4)

    @on(Button.Pressed, "#home-downloads")
    def _home_downloads_pressed(self, event: Button.Pressed) -> None:
        self.query_one(TabbedContent).active = "tab-dl"
        self.query_one("#dl", DataTable).focus()

    @on(DataTable.RowSelected, "#home-channels")
    def _home_creator_picked(self, event: DataTable.RowSelected) -> None:
        saved = subs.load()
        idx = int(event.row_key.value)
        if 0 <= idx < len(saved):
            self.notify(f"Checking {saved[idx].get('title', 'creator')[:40]}…", timeout=2)
            self.run_worker(lambda: self._sync_worker([saved[idx]]), thread=True)

    # ---------------- themes ----------------
    def _apply_theme(self, name):
        target = TEXTUAL_THEMES.get(name, "textual-dark")
        if target in self.available_themes:
            self.theme = target

    def action_cycle_theme(self) -> None:
        cur = self.cfg.get("theme", "youtube")
        nxt = THEME_ORDER[(THEME_ORDER.index(cur) + 1) % len(THEME_ORDER)] \
            if cur in THEME_ORDER else THEME_ORDER[0]
        self.cfg["theme"] = nxt
        save_config(self.cfg)
        self._apply_theme(nxt)
        self.notify(f"Theme: {nxt}", timeout=2)

    # ---------------- type / quality selectors ----------------
    @on(Select.Changed, "#kind")
    def _kind_changed(self, event: Select.Changed) -> None:
        if self._syncing_select or event.value is Select.BLANK:
            return
        kind = event.value
        self.cfg["last_kind"] = kind
        q = self.query_one("#quality", Select)
        self._syncing_select = True
        try:
            q.set_options(self._quality_options(kind))
            q.value = self._default_quality(kind)
        finally:
            self._syncing_select = False
        container = self.query_one("#container", Select)
        container.disabled = kind != "video"
        save_config(self.cfg)

    @on(Select.Changed, "#quality")
    def _quality_changed(self, event: Select.Changed) -> None:
        if self._syncing_select or event.value is Select.BLANK:
            return
        kind = self.query_one("#kind", Select).value
        self.cfg["last_video_quality" if kind == "video" else "last_audio_format"] = event.value
        save_config(self.cfg)

    @on(Select.Changed, "#container")
    def _container_changed(self, event: Select.Changed) -> None:
        if event.value is Select.BLANK:
            return
        self.cfg["video_container"] = event.value if event.value in ("mp4", "mkv") else "mp4"
        save_config(self.cfg)

    def _choice(self):
        kind = self.query_one("#kind", Select).value
        quality = self.query_one("#quality", Select).value
        return kind, quality

    def action_toggle_kind(self) -> None:
        k = self.query_one("#kind", Select)
        k.value = "audio" if k.value == "video" else "video"

    # ---------------- input: URL / channel / search ----------------
    def action_focus_input(self) -> None:
        self.query_one("#url", Input).focus()

    def action_leave_input(self) -> None:
        """Esc: leave a text box so the single-key shortcuts work again."""
        active = self.query_one(TabbedContent).active
        target = {"tab-home": "#home-channels", "tab-dl": "#dl", "tab-search": "#results",
                  "tab-pending": "#pend", "tab-subs": "#subs", "tab-hist": "#hist"}.get(active, "#dl")
        self.query_one(target, DataTable).focus()

    @on(Input.Submitted, "#url")
    def _submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        if text.startswith("@") or (text.startswith(("http://", "https://"))
                                    and subs.is_channel_url(text)):
            self._ask_channel(text)
        elif text.startswith(("http://", "https://")):
            if has_playlist_param(text) and "v=" in text:
                self.push_screen(
                    ChoiceScreen("This link is a video inside a playlist.",
                                 [("one", "Just this video", "success"),
                                  ("all", "Whole playlist", "primary"),
                                  ("cancel", "Cancel", "error")]),
                    lambda c, u=text: self._playlist_param_chosen(c, u))
            else:
                self.run_worker(lambda: self._load_url(text), thread=True)
        else:
            self.notify(f"Searching: {text[:40]}", timeout=2)
            self.run_worker(lambda: self._do_search(text), thread=True)

    def _playlist_param_chosen(self, choice, url):
        if choice == "cancel" or choice is None:
            return
        if choice == "one":
            url = strip_playlist_param(url)
        self.run_worker(lambda: self._load_url(url), thread=True)

    # -- worker threads (never touch widgets directly) --
    def _load_url(self, url):
        info, err = fetch_info_quiet(url, self.cfg)
        if err or not info:
            self.call_from_thread(self.notify, f"Could not fetch: {str(err)[:140]}",
                                  severity="error", timeout=8)
            return
        if info.get("_type") == "playlist":
            self.call_from_thread(self._show_playlist, info.get("title") or "playlist",
                                  get_entries(info))
        else:
            dup = None
            if self.cfg.get("use_archive", True):
                try:
                    dup = find_existing(info, url, download_dir=self.cfg.get("download_dir"))
                except Exception:
                    dup = None
            self.call_from_thread(self._add_single, url, info, dup)

    def _do_search(self, query):
        results = search_youtube(query, n=20, cfg=self.cfg, quiet=True)
        self.call_from_thread(self._show_results, query, results)

    # -- main-thread handlers --
    @_guard
    def _add_single(self, url, info, dup=None):
        if dup and dup.get("state") in ("exists", "same-name"):
            same = "" if dup["state"] == "exists" else "\n(matched by name only)"
            self.push_screen(
                ChoiceScreen(f"Already downloaded:\n{str(info.get('title', url))[:70]}\n\n"
                             f"Location:\n{dup['file']}{same}",
                             [("again", "Download again", "primary"),
                              ("cancel", "Skip", "success")]),
                lambda c, u=url, i=info: self._enqueue_single(u, i) if c == "again" else None)
            return
        self._enqueue_single(url, info)

    @_guard
    def _enqueue_single(self, url, info):
        kind, quality = self._choice()
        self._enqueue(make_job(self.cfg, kind, quality, url, info.get("title"),
                               key=archive_key(info),
                               channel=info.get("uploader") or info.get("channel") or ""))
        self.notify(f"Queued: {str(info.get('title', url))[:60]}", timeout=3)

    @_guard
    def _show_playlist(self, title, entries):
        if not entries:
            self.notify("Playlist is empty.", severity="warning"); return
        seen = archive_set() if self.cfg.get("use_archive", True) else set()
        skipped = {i for i, e in enumerate(entries) if key_for_entry(e) in seen}
        self.push_screen(PlaylistScreen(title, entries, skipped),
                         lambda sel: self._playlist_chosen(title, entries, sel))

    def _playlist_chosen(self, title, entries, selected):
        if not selected:
            return
        kind, quality = self._choice()
        out = Path(self.cfg["download_dir"])
        folders = self.cfg.get("playlist_folders", True)
        for i in selected:
            e = entries[i]
            tmpl = playlist_template(out, title, e["index"]) if folders else None
            self._enqueue(make_job(self.cfg, kind, quality, e["url"], e["title"],
                                   key=key_for_entry(e), outtmpl=tmpl))
        self.notify(f"Queued {len(selected)} video(s) from {title[:40]}", timeout=4)

    # -- channels --
    def _ask_channel(self, text):
        depth = int(self.cfg.get("sync_depth", 15) or 15)
        self.push_screen(
            ChoiceScreen(f"Channel: {text}",
                         [("sub", "Subscribe (new uploads only)", "success"),
                          ("subold", f"Subscribe + get latest {depth}", "primary"),
                          ("latest", f"Just get latest {depth}", "default"),
                          ("cancel", "Cancel", "error")]),
            lambda c, t=text: self._channel_chosen(c, t))

    def _channel_chosen(self, choice, text):
        if choice in (None, "cancel"):
            return
        self.notify("Contacting YouTube…", timeout=2)
        self.run_worker(lambda: self._channel_worker(choice, text), thread=True)

    def _channel_worker(self, choice, text):
        depth = int(self.cfg.get("sync_depth", 15) or 15)
        url = subs.normalize_channel_url(text)
        entries, title = [], ""
        if choice == "latest":
            info, err = subs.fetch_recent(url, self.cfg, n=depth)
            if err or not info:
                self.call_from_thread(self.notify, f"Could not read channel: {str(err)[:120]}",
                                      severity="error"); return
            entries = get_entries(info)[:depth]
            title = subs._clean_title(info.get("title") or "channel")
        else:
            ok, msg, title, all_entries = subs.subscribe(self.cfg, text, choice == "sub")
            self.call_from_thread(self.notify, msg, severity="information" if ok else "error")
            if not ok:
                return
            self.call_from_thread(self._refresh_subs)
            self.call_from_thread(self._refresh_home)
            if choice == "subold":
                entries = all_entries[:depth]
        if entries:
            tmpl = channel_template(Path(self.cfg["download_dir"]), title or "channel")
            self.call_from_thread(self._enqueue_entries, entries, [tmpl] * len(entries))

    @_guard
    def _enqueue_entries(self, entries, tmpls, kind=None, quality=None):
        if not (kind and quality):                       # a subscription may carry its own format
            kind, quality = self._choice()
        for e, tmpl in zip(entries, tmpls):
            self._enqueue(make_job(self.cfg, kind, quality, e["url"], e["title"],
                                   key=key_for_entry(e), outtmpl=tmpl))
        if entries:
            self.notify(f"Queued {len(entries)} video(s)", timeout=3)

    # ---------------- search results ----------------
    @_guard
    def _show_results(self, query, results):
        self._results, self._query = results, query
        self._marked = set()
        self._lo = self._hi = None
        self.query_one("#lenfilter", Input).value = ""
        self._render_results()
        if not results:
            self.notify("No results.", severity="warning"); return
        self.query_one(TabbedContent).active = "tab-search"
        self.query_one("#results", DataTable).focus()

    def _render_results(self, keep_row=None):
        t = self.query_one("#results", DataTable)
        t.clear()
        self._shown_results = filter_results(self._results, self._lo, self._hi)
        for n, r in enumerate(self._shown_results):
            t.add_row(Text("✓" if r["url"] in self._marked else ""), Text(str(n + 1)), Text(r["title"]),
                      Text(fmt_duration(r["duration"]) if r["duration"] else "?"),
                      Text(r.get("uploader") or ""), Text(fmt_views_short(r.get("views"))), key=str(n))
        if keep_row is not None and t.row_count:
            t.move_cursor(row=min(keep_row, t.row_count - 1))
        k = len(self._marked)
        self.sub_title = (f'"{self._query}": {len(self._shown_results)} result(s) — '
                          + (f"{k} marked, Enter downloads them" if k else "Space marks, Enter downloads"))

    def _on_results_tab(self):
        return (self.query_one(TabbedContent).active == "tab-search"
                and self.query_one("#results", DataTable).has_focus)

    def action_mark_result(self) -> None:
        """Space: tick / untick the highlighted search result and step down."""
        if not self._on_results_tab() or not self._shown_results:
            return
        t = self.query_one("#results", DataTable)
        row = t.cursor_row
        if not 0 <= row < len(self._shown_results):
            return
        url = self._shown_results[row]["url"]
        self._marked.symmetric_difference_update({url})
        self._render_results(keep_row=row + 1)

    def action_mark_all(self) -> None:
        """a: mark every result shown (or clear the marks if they all are)."""
        if not self._on_results_tab() or not self._shown_results:
            return
        urls = {r["url"] for r in self._shown_results}
        if urls <= self._marked:
            self._marked -= urls
        else:
            self._marked |= urls
        self._render_results(keep_row=self.query_one("#results", DataTable).cursor_row)

    def _queue_marked(self):
        picks = [r for r in self._results if r["url"] in self._marked]
        idx = DupIndex()
        seen = archive_set() if self.cfg.get("use_archive", True) else set()
        kind, quality = self._choice()
        queued = skipped = 0
        for r in picks:
            have = find_existing({"id": r.get("id"), "extractor_key": "youtube", "title": r["title"]},
                                 r["url"], index=idx, scan=False)
            key = key_for_entry(r)
            if (have and have["state"] == "exists") or (key and key in seen):
                skipped += 1
                continue
            self._enqueue(make_job(self.cfg, kind, quality, r["url"], r["title"],
                                   key=key, channel=r.get("uploader") or ""))
            if key:
                seen.add(key)
            queued += 1
        self._marked.clear()
        self._render_results(keep_row=self.query_one("#results", DataTable).cursor_row)
        msg = f"Queued {queued} video(s)"
        if skipped:
            msg += f" · skipped {skipped} you already have"
        self.notify(msg, timeout=4)

    @on(Input.Submitted, "#lenfilter")
    def _length_filter(self, event: Input.Submitted) -> None:
        try:
            self._lo, self._hi = parse_length_filter(event.value)
        except ValueError as e:
            self.notify(f"Invalid filter: {e}", severity="error"); return
        self._render_results()
        self.query_one("#results", DataTable).focus()

    @on(DataTable.RowSelected, "#results")
    def _result_picked(self, event: DataTable.RowSelected) -> None:
        if self._marked:                        # ticked results win over the highlighted row
            self._queue_marked()
            return
        idx = int(event.row_key.value)
        if not 0 <= idx < len(self._shown_results):
            return
        r = self._shown_results[idx]
        key = key_for_entry(r)
        if self.cfg.get("use_archive", True) and key and key in archive_set():
            self.notify("Already in download archive; not queued.", timeout=3)
            return
        kind, quality = self._choice()
        self._enqueue(make_job(self.cfg, kind, quality, r["url"], r["title"],
                               key=key, channel=r.get("uploader") or ""))
        self.notify(f"Queued: {r['title'][:60]}", timeout=3)

    # ---------------- pending queue ----------------
    @_guard
    def _refresh_pending(self):
        t = self.query_one("#pend", DataTable)
        t.clear()
        for i, it in enumerate(pending.all_items()):
            t.add_row(Text(str(i + 1)), Text(it.get("title") or it.get("url", "?")),
                      Text(it.get("added", "")), key=str(i))

    @on(DataTable.RowSelected, "#pend")
    def _pending_picked(self, event: DataTable.RowSelected) -> None:
        items = pending.all_items()
        idx = int(event.row_key.value)
        if not 0 <= idx < len(items):
            return
        it = items[idx]
        kind, quality = self._choice()
        self._enqueue(make_job(self.cfg, kind, quality, it["url"], it.get("title"),
                               key=""))
        pending.remove_by_url(it["url"])
        self._refresh_pending()
        self.notify(f"Queued: {str(it.get('title', ''))[:60]}", timeout=3)

    # ---------------- subscriptions ----------------
    @_guard
    def _refresh_subs(self):
        table = self.query_one("#subs", DataTable)
        selected = table.cursor_row if table.row_count else 0
        table.clear()
        for i, creator in enumerate(subs.load()):
            table.add_row(Text(creator.get("title", "?")),
                          Text(creator.get("last_sync") or "Not checked yet"),
                          Text(creator.get("url", "")), key=str(i))
        if table.row_count:
            table.move_cursor(row=min(selected, table.row_count - 1))

    def _selected_subscription(self):
        table = self.query_one("#subs", DataTable)
        creators = subs.load()
        if table.row_count == 0 or not 0 <= table.cursor_row < table.row_count:
            return None
        try:
            row_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
            index = int(row_key)
        except (ValueError, TypeError, AttributeError):
            index = table.cursor_row
        return creators[index] if 0 <= index < len(creators) else None

    @on(Button.Pressed, "#sub-add")
    def _sub_add_pressed(self, event: Button.Pressed) -> None:
        self.push_screen(ChannelAddScreen(), self._sub_add_completed)

    def _sub_add_completed(self, target):
        if target:
            self._ask_channel(target)

    @on(Button.Pressed, "#sub-remove")
    def _sub_remove_pressed(self, event: Button.Pressed) -> None:
        creator = self._selected_subscription()
        if not creator:
            self.notify("Select a saved channel first.", severity="warning")
            return
        title = creator.get("title", "this creator")
        self.push_screen(ChoiceScreen(
            f"Remove {title} from your saved creators?\nThis only removes the subscription; downloaded files stay untouched.",
            [("remove", "Remove subscription", "error"), ("cancel", "Keep it", "success")]),
            lambda choice, url=creator.get("url", ""): self._remove_subscription(url)
            if choice == "remove" else None)

    @_guard
    def _remove_subscription(self, url):
        title = subs.remove_sub(url)
        self._refresh_subs()
        self._refresh_home()
        self.notify(f"Removed {title}" if title else "That subscription was not found.",
                    severity="information" if title else "warning", timeout=4)

    @on(Button.Pressed, "#sub-explore")
    def _sub_explore_pressed(self, event: Button.Pressed) -> None:
        creator = self._selected_subscription()
        if not creator:
            self.notify("Select a saved channel first.", severity="warning")
            return
        self._explore_subscription(creator)

    @on(Button.Pressed, "#sub-check")
    def _sub_check_pressed(self, event: Button.Pressed) -> None:
        creator = self._selected_subscription()
        if not creator:
            self.notify("Select a saved channel first.", severity="warning")
            return
        self._check_subscriptions([creator])

    @on(Button.Pressed, "#sub-check-all")
    def _sub_check_all_pressed(self, event: Button.Pressed) -> None:
        self.action_sync()

    @on(DataTable.RowSelected, "#subs")
    def _sub_picked(self, event: DataTable.RowSelected) -> None:
        creator = self._selected_subscription()
        if creator:
            self._explore_subscription(creator)

    def _explore_subscription(self, creator):
        title = creator.get("title", "creator")
        self.notify(f"Exploring {title[:40]}…", timeout=2)
        self.run_worker(lambda: self._explore_subscription_worker(creator), thread=True)

    def _explore_subscription_worker(self, creator):
        count = min(50, max(15, int(self.cfg.get("sync_depth", 15) or 15)))
        info, error = subs.fetch_recent(creator["url"], self.cfg, n=count)
        if error or not info:
            self.call_from_thread(self.notify, f"Could not explore channel: {str(error)[:120]}",
                                  severity="error", timeout=7)
            return
        entries = get_entries(info)
        title = subs._clean_title(info.get("title") or creator.get("title") or "Creator")
        self.call_from_thread(self._show_creator_explore, title, creator, entries)

    @_guard
    def _show_creator_explore(self, title, creator, entries):
        if not entries:
            self.notify(f"No recent videos found for {title}.", severity="warning", timeout=4)
            return
        known = archive_set() if self.cfg.get("use_archive", True) else set()
        skipped = {i for i, entry in enumerate(entries)
                   if (key_for_entry(entry) and key_for_entry(entry) in known)}
        self.push_screen(ChannelExploreScreen(title, entries, skipped),
                         lambda indexes: self._queue_creator_videos(title, entries, indexes))

    def _queue_creator_videos(self, title, entries, indexes):
        if not indexes:
            return
        kind, quality = self._choice()
        template = channel_template(Path(self.cfg["download_dir"]), title)
        for index in indexes:
            entry = entries[index]
            self._enqueue(make_job(self.cfg, kind, quality, entry["url"], entry.get("title"),
                                   key=key_for_entry(entry), channel=title, outtmpl=template))
        self.notify(f"Queued {len(indexes)} video(s) from {title}", timeout=4)

    def _check_subscriptions(self, creators):
        if not creators:
            self.notify("No saved creators yet. Choose Add channel to begin.", severity="warning")
            return
        self.notify(f"Checking {len(creators)} creator(s) for new uploads…", timeout=3)
        self.run_worker(lambda: self._sync_worker(creators), thread=True)

    def action_sync(self) -> None:
        all_subs = subs.load()
        if not all_subs:
            self.notify("No subscriptions. Paste a channel URL to add one.", severity="warning")
            return
        self.notify(f"Syncing {len(all_subs)} channel(s)…", timeout=3)
        self.run_worker(lambda: self._sync_worker(all_subs), thread=True)

    def _sync_worker(self, which):
        seen = archive_set()
        total, errors = 0, 0
        stamp = time.strftime("%Y-%m-%d %H:%M")
        for s in which:
            new, skipped, err = subs.plan_sub(self.cfg, s, seen)
            if skipped:                                  # filtered out: remember, don't re-check every sync
                archive_add([key_for_entry(e) for e in skipped])
            if err:
                errors += 1
                self.call_from_thread(self.notify, f"{s.get('title', '?')[:30]}: {err[:80]}",
                                      severity="error"); continue
            tmpl = channel_template(Path(self.cfg["download_dir"]), s.get("title", "channel"))
            if new:
                kind, quality = subs.override_for(s)
                self.call_from_thread(self._enqueue_entries, new, [tmpl] * len(new), kind, quality)
                seen.update(k for k in (key_for_entry(e) for e in new) if k)
                total += len(new)
        # Serialize the read-modify-write so a concurrent CLI edit cannot be lost.
        with subs.file_lock(subs.SUBS_FILE):
            stored = subs.load()
            urls = {s["url"] for s in which}
            for s in stored:
                if s.get("url") in urls:
                    s["last_sync"] = stamp
            subs.save(stored)
        self.call_from_thread(self._refresh_subs)
        self.call_from_thread(self._refresh_home)
        self.call_from_thread(self.notify,
                              f"Sync done: {total} new video(s)" + (f", {errors} error(s)" if errors else ""),
                              severity="warning" if errors else "information", timeout=5)

    # ---------------- history ----------------
    @_guard
    def _refresh_history(self):
        hist = load_history()
        t = self.query_one("#hist", DataTable)
        t.clear()
        for e in list(reversed(hist))[:300]:
            t.add_row(Text(e.get("date", "")), Text(e.get("kind", "")), Text(str(e.get("quality", ""))),
                      Text(fmt_size(e["size"]) if e.get("size") else "—"),
                      Text(e.get("channel", "") or ""), Text(e.get("title", "?")))
        total = sum((e.get("size") or 0) for e in hist)
        self.query_one("#hist-summary", Static).update(
            f"{len(hist)} downloads · {fmt_size(total) if total else '—'} total (newest 300 shown)")

    @on(TabbedContent.TabActivated)
    def _tab_changed(self, event: TabbedContent.TabActivated) -> None:
        pid = event.pane.id
        if pid == "tab-home":
            self._refresh_home()
        elif pid == "tab-hist":
            self._refresh_history()
        elif pid == "tab-pending":
            self._refresh_pending()
        elif pid == "tab-subs":
            self._refresh_subs()

    def action_tab(self, pane_id: str) -> None:
        self.query_one(TabbedContent).active = pane_id
        target = {"tab-home": "#home-channels", "tab-dl": "#dl", "tab-search": "#results",
                  "tab-pending": "#pend", "tab-subs": "#subs", "tab-hist": "#hist"}[pane_id]
        self.query_one(target, DataTable).focus()

    # ---------------- download table ----------------
    def _enqueue(self, job):
        n = self._next_id
        self._next_id += 1
        key = str(n)
        job.idx = n
        self.jobs.append(job)
        self._rows[id(job)] = key
        self._by_row[key] = job
        file_choice = (f"{job.quality}p · {job.opts.get('merge_output_format', self.cfg.get('video_container', 'mp4')).upper()}"
                       if job.kind == "video" and str(job.quality).isdigit() else
                       (f"{job.quality} · {job.opts.get('merge_output_format', self.cfg.get('video_container', 'mp4')).upper()}"
                        if job.kind == "video" else str(job.quality)))
        self.query_one("#dl", DataTable).add_row(
            Text(str(n)), Text(job.title), Text(job.kind), *self._cells(job), Text(file_choice), key=key)
        self._shown[id(job)] = self._cells_key(job)
        self.runner.submit(job)

    @staticmethod
    def _size_text(job):
        return fmt_size(job.size) if job.size else ""

    def _cells_key(self, job):
        return (job.status, job.pct, job.speed, job.eta, job.size, job.error)

    def _cells(self, job):
        label, style = _STATUS.get(job.status, (job.status, ""))
        if job.status == "error" and job.error:
            label = "✗ " + job.error[:22]
        pct = 100 if job.status == "done" else job.pct
        show_speed = job.status == "downloading"
        return (Text(label, style=style), Text(_bar(pct)),
                Text(job.speed if show_speed else ""), Text(job.eta if show_speed else ""),
                Text(self._size_text(job)))

    @_guard
    def _tick(self) -> None:
        t = self.query_one("#dl", DataTable)
        for job in self.jobs:
            key = self._cells_key(job)
            if self._shown.get(id(job)) == key:
                continue
            self._shown[id(job)] = key
            rk = self._rows[id(job)]
            try:
                for col, val in zip(("status", "prog", "speed", "eta", "size"), self._cells(job)):
                    t.update_cell(rk, col, val)
            except Exception:
                pass                                # row was cleared meanwhile
        active = sum(1 for j in self.jobs if j.status in ("downloading", "processing"))
        queued = sum(1 for j in self.jobs if j.status in ("queued", "paused"))
        done = sum(1 for j in self.jobs if j.status == "done")
        failed = sum(1 for j in self.jobs if j.status == "error")
        if self.jobs and not self._shown_results:
            self.sub_title = f"⬇ {active}  ⏳ {queued}  ✓ {done}  ✗ {failed}"
        home_state = (active, queued, done, len(pending.all_items()))
        if home_state != getattr(self, "_home_counts", None):
            self._update_home_hero()

    def _selected_job(self):
        t = self.query_one("#dl", DataTable)
        if t.row_count == 0 or not 0 <= t.cursor_row < t.row_count:
            return None
        rk = t.coordinate_to_cell_key(Coordinate(t.cursor_row, 0)).row_key.value
        return self._by_row.get(rk)

    # -- worker-thread callback when a job finishes --
    def _thread_finished(self, job):
        hook_ok = True
        if job.status == "error":
            _record_failed(job)
        if job.status == "done":
            try:
                hook_ok = _record_done(self.cfg, job, quiet=True)
            except Exception:
                hook_ok = True
        if self.is_running:
            self.call_from_thread(self._after_finish, job, hook_ok)

    @_guard
    def _after_finish(self, job, hook_ok):
        self._refresh_home()
        if job.status == "done":
            self.notify(f"✓ {job.title[:60]}", timeout=3)
            if not hook_ok:
                self.notify("Post-download hook failed.", severity="warning")
        elif job.status == "error":
            self.notify(f"✗ {job.title[:40]}: {job.error}", severity="error", timeout=8)

    # ---------------- job actions ----------------
    def action_toggle_pause(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        if job.status == "downloading":
            job._pause.clear(); job.status = "paused"
        elif job.status == "paused":
            job._pause.set(); job.status = "downloading" if job.pct else "queued"
        else:
            self.notify("Only a running download can be paused.", timeout=2)

    def action_cancel_job(self) -> None:
        job = self._selected_job()
        if job is None or job.status in _FINISHED:
            return
        job._cancel.set(); job._pause.set()
        if job.status in ("queued", "paused"):
            job.status = "cancelled"

    def action_retry(self) -> None:
        job = self._selected_job()
        todo = [job] if job and job.status in ("error", "cancelled") else \
               [j for j in self.jobs if j.status == "error"]
        for j in todo:
            j.reset()
            self.runner.submit(j)
        self.notify(f"Retrying {len(todo)} download(s)" if todo else "Nothing to retry.", timeout=2)

    def action_clear_done(self) -> None:
        t = self.query_one("#dl", DataTable)
        for job in [j for j in self.jobs if j.status in ("done", "cancelled")]:
            self._drop(t, job)

    def _drop(self, table, job):
        rk = self._rows.pop(id(job), None)
        self._shown.pop(id(job), None)
        if rk is not None:
            self._by_row.pop(rk, None)
            try:
                table.remove_row(rk)
            except Exception:
                pass
        if job in self.jobs:
            self.jobs.remove(job)

    def action_open_file(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        fp = Path(job.filepath) if job.filepath else None
        if fp and fp.exists():
            open_path(fp)
        else:
            open_path(Path(self.cfg["download_dir"]))

    def action_delete_row(self) -> None:
        active = self.query_one(TabbedContent).active
        if active == "tab-pending":
            t = self.query_one("#pend", DataTable)
            if t.row_count:
                items = pending.all_items()
                i = int(t.coordinate_to_cell_key(Coordinate(t.cursor_row, 0)).row_key.value)
                if 0 <= i < len(items):
                    pending.remove_by_url(items[i]["url"])
                self._refresh_pending()
        elif active == "tab-subs":
            self._sub_remove_pressed(None)
        elif active == "tab-dl":
            job = self._selected_job()
            if job and job.status in _FINISHED:
                self._drop(self.query_one("#dl", DataTable), job)

    # ---------------- quit ----------------
    def _active_jobs(self):
        return [j for j in self.jobs if j.status not in _FINISHED]

    async def action_quit(self) -> None:
        running = self._active_jobs()
        if not running:
            self._shutdown_and_exit(); return
        self.push_screen(
            ChoiceScreen(f"{len(running)} download(s) still in progress.",
                         [("quit", "Cancel them & quit", "error"),
                          ("stay", "Keep downloading", "success")]),
            lambda c: self._shutdown_and_exit() if c == "quit" else None)

    def on_unmount(self) -> None:
        timer = getattr(self, "_tick_timer", None)
        if timer is not None:
            timer.stop()

    def _shutdown_and_exit(self):
        self.runner.shutdown(self.jobs)
        self.exit()


def run_tui(cfg):
    Path(cfg["download_dir"]).mkdir(parents=True, exist_ok=True)
    YtGrabApp(cfg).run()
