"""My channels: creators you keep for later, to explore like a channel page, with no YouTube account.

Saving a channel only remembers it on this computer. Nothing is downloaded automatically
(``auto`` is False, so `ytgrab sync` ignores these) and nobody is "subscribed to" on YouTube.
Open a channel to browse its tabs (Home, Videos, Shorts, Live, Playlists, Podcasts), tick videos
with Space and download them together.
"""

import contextlib
import re
import sys
import threading
import time

from . import subs, termui
from .config import file_lock
from .fmt import fmt_duration
from .home import LineEditor
from .keys import open_keys
from .library import archive_set
from .theme import C, _bg, _vis, paint

TABS = (
    ("home", "Home", "/featured"),
    ("videos", "Videos", "/videos"),
    ("shorts", "Shorts", "/shorts"),
    ("live", "Live", "/streams"),
    ("playlists", "Playlists", "/playlists"),
    ("podcasts", "Podcasts", "/podcasts"),
)
_TAB_TAIL = re.compile(r"/(featured|videos|shorts|streams|playlists|podcasts|releases|community|about)/?$")
_SEEN_KEEP = 60


# ======================================================================
# data
# ======================================================================
def channel_base(url):
    """https://youtube.com/@x/videos?x=1  ->  https://youtube.com/@x"""
    url = (url or "").split("?")[0].split("#")[0].rstrip("/")
    return _TAB_TAIL.sub("", url)


def tab_url(base, tab_id):
    suffix = next((s for t, _, s in TABS if t == tab_id), "/videos")
    return channel_base(base) + suffix


def to_item(e):
    """A yt-dlp flat entry as a small dict the screens understand (or None)."""
    if not e:
        return None
    vid = e.get("id")
    url = e.get("url") or e.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
    if not url:
        return None
    is_pl = (e.get("_type") == "playlist" or "/playlist?list=" in url
             or str(e.get("ie_key") or "").lower() in ("youtubetab", "youtubeplaylist"))
    return {
        "id": vid, "url": url, "title": e.get("title") or "(untitled)",
        "kind": "playlist" if is_pl else "video",
        "duration": e.get("duration") or 0, "views": e.get("view_count"),
        "count": e.get("playlist_count") or e.get("n_entries"),
        "uploader": e.get("uploader") or e.get("channel") or "",
    }


def _entries(info):
    out, seen = [], set()
    for e in (info or {}).get("entries") or []:
        it = to_item(e)
        if it and it["url"] not in seen:
            seen.add(it["url"])
            out.append(it)
    return out


def _fetch(url, cfg, n):
    return subs.fetch_recent(url, cfg, n=n)


def fetch_tab(cfg, base, tab_id, n=40, fetcher=_fetch):
    """-> (items, error). A tab a channel does not have is simply empty, not an error."""
    info, err = fetcher(tab_url(base, tab_id), cfg, n)
    if err or info is None:
        text = str(err or "")
        if re.search(r"does not have|no .* tab|not exist|Unable to recognize tab", text, re.I):
            return [], None
        return [], text or "could not load"
    return _entries(info), None


def fetch_playlist(cfg, url, n=200, fetcher=_fetch):
    info, err = fetcher(url, cfg, n)
    if err or info is None:
        return [], str(err or "could not load")
    return [i for i in _entries(info) if i["kind"] == "video"], None


def saved():
    return subs.load()


def count():
    try:
        return len(subs.load())
    except Exception:
        return 0


def add_channel(cfg, target, fetcher=_fetch):
    """Save a channel for later. Returns (ok, message, record)."""
    url = subs.normalize_channel_url(target)
    if not url.startswith(("http://", "https://")):
        return False, "Give a channel link or an @handle.", None
    base = channel_base(url)
    for s in subs.load():
        if channel_base(s.get("url")) == base:
            return False, f"{s.get('title') or base} is already in your channels.", s
    info, err = fetcher(tab_url(base, "videos"), cfg, 30)
    if err or not info:
        return False, f"Could not read that channel: {str(err)[:120]}", None
    title = subs._clean_title(info.get("title") or info.get("uploader")) or base
    rec = {"url": tab_url(base, "videos"), "title": title, "added": time.strftime("%Y-%m-%d %H:%M"),
           "last_sync": "", "auto": False, "saved": True,
           "last_ids": [i["id"] for i in _entries(info) if i["id"]][:_SEEN_KEEP]}
    with file_lock(subs.SUBS_FILE):
        cur = subs.load()
        if any(channel_base(s.get("url")) == base for s in cur):
            return False, f"{title} is already in your channels.", None
        cur.append(rec)
        subs.save(cur)
    return True, f"Saved {title}", rec


def remove_channel(target):
    title = subs.remove_sub(target)
    return (True, f"Removed {title}") if title else (False, "No such channel.")


def search_channels(cfg, query, n=6, fetcher=_fetch):
    """Find channels by name -> [{"title", "url"}]."""
    from urllib.parse import quote
    url = f"https://www.youtube.com/results?search_query={quote(query)}&sp=EgIQAg%253D%253D"
    info, err = fetcher(url, cfg, n)
    if err or not info:
        return []
    out = []
    for e in (info.get("entries") or []):
        u = (e or {}).get("url") or ""
        if "/channel/" in u or "/@" in u or "/user/" in u or "/c/" in u:
            out.append({"title": e.get("title") or e.get("channel") or u, "url": u})
    return out[:n]


def mark_visited(url, ids):
    """Remember which videos you have seen on a channel (for the "new" badge)."""
    base = channel_base(url)
    with file_lock(subs.SUBS_FILE):
        cur = subs.load()
        for s in cur:
            if channel_base(s.get("url")) == base:
                s["last_ids"] = list(dict.fromkeys(list(ids) + s.get("last_ids", [])))[:_SEEN_KEEP]
                s["last_visit"] = time.strftime("%Y-%m-%d %H:%M")
        subs.save(cur)


def new_count(cfg, rec, fetcher=_fetch):
    """How many of the channel's latest videos you have not seen yet. -> (count, error)"""
    items, err = fetch_tab(cfg, rec["url"], "videos", 20, fetcher)
    if err:
        return 0, err
    known = set(rec.get("last_ids") or [])
    if not known:                                   # nothing remembered yet: everything would be "new"
        return 0, None
    return sum(1 for i in items if i["id"] and i["id"] not in known), None


# ======================================================================
# explorer screen
# ======================================================================
class Explorer:
    """State of one channel page (pure data + methods; drawing is `render`)."""

    def __init__(self, rec, seen_ids=(), downloaded=frozenset()):
        self.rec = rec
        self.title = rec.get("title") or rec.get("url")
        self.tab = 1                              # Videos
        self.items = {}                           # tab id -> list
        self.errors = {}
        self.loading = None
        self.cursor = {t: 0 for t, _, _ in TABS}
        self.marks = set()
        self.stack = []                           # drilled-in playlists: (title, items, cursor)
        self.seen = set(seen_ids)
        self.downloaded = downloaded
        self.message = ""

    # --- what is on screen
    @property
    def tab_id(self):
        return TABS[self.tab][0]

    def current_items(self):
        if self.stack:
            return self.stack[-1][1]
        return self.items.get(self.tab_id)

    def get_cursor(self):
        return self.stack[-1][2] if self.stack else self.cursor[self.tab_id]

    def set_cursor(self, i):
        if self.stack:
            t, items, _ = self.stack[-1]
            self.stack[-1] = (t, items, i)
        else:
            self.cursor[self.tab_id] = i

    def move(self, delta):
        items = self.current_items() or []
        if items:
            self.set_cursor(max(0, min(len(items) - 1, self.get_cursor() + delta)))

    def switch_tab(self, delta):
        if self.stack:
            return
        self.tab = (self.tab + delta) % len(TABS)
        self.message = ""

    def toggle(self):
        items = self.current_items() or []
        i = self.get_cursor()
        if 0 <= i < len(items) and items[i]["kind"] == "video":
            self.marks.symmetric_difference_update({items[i]["url"]})
            self.move(1)

    def mark_all(self):
        vids = {i["url"] for i in (self.current_items() or []) if i["kind"] == "video"}
        if vids <= self.marks:
            self.marks -= vids
        else:
            self.marks |= vids

    def picked(self):
        """Videos to act on: the ticked ones, else the highlighted one."""
        items = self.current_items() or []
        vids = [i for i in items if i["url"] in self.marks]
        if vids:
            return vids
        i = self.get_cursor()
        if 0 <= i < len(items) and items[i]["kind"] == "video":
            return [items[i]]
        return []

    def highlighted(self):
        items = self.current_items() or []
        i = self.get_cursor()
        return items[i] if 0 <= i < len(items) else None


def _active_tab(label):
    """The open tab: bold on a lighter background, so it stands out from the rest."""
    return _bg(48, 52, 70) + paint(f" {label} ", C.WHITE, C.BOLD) + C.R if _bg(0, 0, 0) else paint(f"[{label}]", C.WHITE, C.BOLD)


def render_explorer(st, cols, rows, t=0.0):
    avail = max(10, cols - 1)
    pad = "  "
    lines = [""]
    head = pad + paint("◂ ", C.GREY_D) + paint(st.title, C.WHITE, C.BOLD)
    if st.stack:
        head += paint("  ›  ", C.GREY_D) + paint(st.stack[-1][0], C.AMBER, C.BOLD)
    lines.append(head)
    if not st.stack:
        tabs = []
        for i, (_, label, _) in enumerate(TABS):
            tabs.append(_active_tab(label) if i == st.tab else paint(f" {label} ", C.GREY))
        bar = pad + paint("│", C.BORDER).join(tabs)
        lines.append(bar if _vis(bar) <= avail else pad + paint(f" ◂ {TABS[st.tab][1]} ▸ ", C.WHITE, C.BOLD))
    else:
        lines.append(pad + paint("playlist  ·  ← back", C.GREY_D))
    lines.append(pad + paint("─" * min(avail - 2, 90), C.BORDER))

    items = st.current_items()
    body_rows = max(3, rows - len(lines) - 3)
    if st.loading == st.tab_id and items is None:
        lines.append(pad + paint(termui.spinner(t) + "  Loading " + TABS[st.tab][1] + "…", C.AMBER))
    elif items is None:
        lines.append(pad + paint("Press → or r to load", C.GREY_D))
    elif st.errors.get(st.tab_id) and not items:
        lines.append(pad + paint("Could not load this tab: " + termui.clip(str(st.errors[st.tab_id]), avail - 30), C.RED))
    elif not items:
        lines.append(pad + paint(f"Nothing here: this channel has no {TABS[st.tab][1].lower()}.", C.GREY_D))
    else:
        cur = st.get_cursor()
        top = max(0, min(cur - body_rows // 2, len(items) - body_rows))
        for idx in range(top, min(len(items), top + body_rows)):
            it = items[idx]
            on = idx == cur
            marked = it["url"] in st.marks
            done = it.get("id") and any(k.endswith(" " + str(it["id"])) for k in st.downloaded)
            is_new = it["kind"] == "video" and it.get("id") and st.seen and it["id"] not in st.seen
            box = (paint("◉ ", C.RED_HOT, C.BOLD) if marked else paint("○ ", C.GREY_D)) if it["kind"] == "video" \
                else paint("▤ ", C.AMBER)
            right = ""
            if it["kind"] == "video":
                right = fmt_duration(it["duration"]) if it["duration"] else "live" if st.tab_id == "live" else ""
            elif it.get("count"):
                right = f"{it['count']} videos"
            tags = (paint(" NEW", C.GREEN, C.BOLD) if is_new else "") + (paint(" ✓", C.GREEN) if done else "")
            tag_len = (4 if is_new else 0) + (2 if done else 0)
            tw = max(8, avail - 2 - 2 - 2 - 2 - len(right) - 2 - tag_len - 2)
            title = termui.clip(it["title"], tw)
            row = (pad + (paint("▸ ", C.RED_HOT, C.BOLD) if on else "  ") + box
                   + (paint(title, C.WHITE, C.BOLD) if on else paint(title, C.OFF)) + tags)
            if right:
                gap = max(1, avail - len(pad) - 2 - 2 - len(title) - tag_len - len(right) - 1)
                row += " " * gap + paint(right, C.AMBER)
            lines.append(row)
        lines.append(pad + paint(f"{cur + 1}/{len(items)}", C.GREY_D)
                     + (paint(f"   {len(st.marks)} marked", C.AMBER, C.BOLD) if st.marks else ""))
    foot = ("←→ tab" if not st.stack else "← back") + "   ↑↓ move   Space mark   a all   Enter " + \
        ("open" if (st.highlighted() or {}).get("kind") == "playlist" and not st.marks else "download") + \
        "   r reload   q back"
    body = lines[:max(1, rows - 2)]
    while len(body) < rows - 2:
        body.append("")
    body.append(pad + paint(termui.clip(st.message, avail - 2), C.AMBER) if st.message else "")
    body.append(pad + paint(termui.clip(foot, avail - 2), C.GREY_D))
    return [termui.clip_ansi(ln, avail) for ln in body]


def explore_channel(cfg, rec, on_download=None, reader="auto", out=None, fetcher=_fetch, alt=True,
                    close_reader=True):
    """Browse one channel. `on_download(items)` is called with the picked videos (the screen is
    left first, so it may print and prompt)."""
    if reader == "auto":
        reader = open_keys()
    if reader is None:
        print(paint("  The channel explorer needs an interactive terminal.", C.AMBER))
        return
    base = rec["url"]
    st = Explorer(rec, rec.get("last_ids") or [], _downloaded_keys())
    results = {}
    lock = threading.Lock()

    def load(tab_id, force=False):
        if not force and st.items.get(tab_id) is not None:
            return
        st.loading = tab_id
        st.items.pop(tab_id, None)

        def work():
            items, err = fetch_tab(cfg, base, tab_id, 60, fetcher)
            with lock:
                results[tab_id] = (items, err)
        threading.Thread(target=work, daemon=True).start()

    def drill(item):
        st.loading = "drill"

        def work():
            items, err = fetch_playlist(cfg, item["url"], 200, fetcher)
            with lock:
                results["drill"] = (item["title"], items, err)
        threading.Thread(target=work, daemon=True).start()

    t0 = time.monotonic()
    out = out or sys.stdout
    try:
        with (termui.alt_screen(out) if alt else contextlib.nullcontext()):
            load(st.tab_id)
            while True:
                with lock:
                    for k in list(results):
                        if k == "drill":
                            title, items, err = results.pop(k)
                            st.loading = None
                            if err:
                                st.message = f"Could not open the playlist: {err[:80]}"
                            else:
                                st.stack.append((title, items, 0))
                        else:
                            items, err = results.pop(k)
                            st.items[k] = items
                            st.errors[k] = err
                            if st.loading == k:
                                st.loading = None
                cols, lines = termui.size()
                termui.draw_full(render_explorer(st, cols, lines, time.monotonic() - t0), out, lines)
                tok = reader.read(0.08)
                if tok is None:
                    continue
                st.message = ""
                if tok in ("q", "CTRL_C", "CTRL_D"):
                    break
                if tok in ("ESC", "BACKSPACE"):
                    if st.stack:
                        st.stack.pop()
                        continue
                    break
                if tok == "LEFT" and st.stack:
                    st.stack.pop()
                elif tok in ("RIGHT", "TAB"):
                    st.switch_tab(1)
                    load(st.tab_id)
                elif tok in ("LEFT", "BTAB"):
                    st.switch_tab(-1)
                    load(st.tab_id)
                elif tok in ("DOWN", "j"):
                    st.move(1)
                elif tok in ("UP", "k"):
                    st.move(-1)
                elif tok == "PGDN":
                    st.move(8)
                elif tok == "PGUP":
                    st.move(-8)
                elif tok == "HOME":
                    st.set_cursor(0)
                elif tok == "END":
                    st.set_cursor(len(st.current_items() or []) - 1)
                elif tok == " ":
                    st.toggle()
                elif tok == "a":
                    st.mark_all()
                elif tok == "r":
                    if st.stack:
                        st.message = "Reload from the channel tab."
                    else:
                        load(st.tab_id, force=True)
                elif tok == "ENTER":
                    hi = st.highlighted()
                    if hi and hi["kind"] == "playlist" and not st.marks:
                        drill(hi)
                        continue
                    picks = st.picked()
                    if not picks:
                        st.message = "Nothing to download here."
                        continue
                    if on_download is None:
                        st.message = "Download is not available in this view."
                        continue
                    # leave the explorer screen so the download flow can print and ask questions
                    out.write("\033[?25h\033[?1049l"); out.flush()
                    try:
                        on_download(picks)
                    finally:
                        out.write("\033[?1049h\033[?25l\033[2J\033[H"); out.flush()
                    st.marks.clear()
                    st.downloaded = _downloaded_keys()
    finally:
        if close_reader:
            try:
                reader.close()
            except Exception:
                pass
        vids = st.items.get("videos") or []
        if vids:
            try:
                mark_visited(rec["url"], [i["id"] for i in vids if i["id"]])
            except Exception:
                pass


def _downloaded_keys():
    try:
        return archive_set()
    except Exception:
        return set()


# ======================================================================
# the channel list ("My channels")
# ======================================================================
class ChannelList:
    """State of the My channels screen. Modes: list, input (add), pick (search results), confirm (remove)."""

    def __init__(self):
        self.recs = saved()
        self.cursor = 0
        self.mode = "list"
        self.editor = LineEditor()
        self.results = []
        self.message = ""
        self.busy = ""                      # text shown with a spinner while something runs
        self.new = {}                       # channel url -> count | None (unknown) | "…" (checking)

    def reload(self):
        self.recs = saved()
        self.cursor = max(0, min(self.cursor, len(self.recs) - 1))

    def move(self, d):
        if self.recs:
            self.cursor = (self.cursor + d) % len(self.recs)

    def current(self):
        return self.recs[self.cursor] if 0 <= self.cursor < len(self.recs) else None


def render_channel_list(st, cols, rows, t=0.0):
    avail = max(10, cols - 1)
    pad = "  "
    lines = ["", pad + paint("My channels", C.WHITE, C.BOLD)
             + paint(f"   {len(st.recs)} saved · kept on this computer only · no YouTube account", C.GREY_D),
             pad + paint("─" * min(avail - 2, 90), C.BORDER)]
    if not st.recs:
        lines += ["", pad + paint("No channels yet.", C.GREY),
                  pad + paint("Press  a  to save one: type an @handle, paste a link, or just a name to search.", C.GREY_D),
                  pad + paint("Saving only remembers it here. Nothing is subscribed on YouTube or downloaded.", C.GREY_D)]
    for i, rec in enumerate(st.recs[:max(1, rows - 9)]):
        on = i == st.cursor
        n = st.new.get(rec["url"])
        badge = ""
        if n == "…":
            badge = paint(termui.spinner(t) + " checking", C.AMBER)
        elif isinstance(n, int) and n > 0:
            badge = paint(f"● {n} new", C.GREEN, C.BOLD)
        elif n == 0:
            badge = paint("up to date", C.GREY_D)
        elif n is None and rec["url"] in st.new:
            badge = paint("could not check", C.RED)
        handle = channel_base(rec["url"]).rsplit("/", 1)[-1]
        name_w = max(10, min(32, avail // 3))
        row = (pad + (paint("▸ ", C.RED_HOT, C.BOLD) if on else "  ") + paint(f"{i + 1:>2}  ", C.RED, C.BOLD)
               + (paint(termui.clip(rec.get("title", ""), name_w).ljust(name_w), C.WHITE, C.BOLD) if on
                  else paint(termui.clip(rec.get("title", ""), name_w).ljust(name_w), C.OFF))
               + "  " + paint(termui.clip(handle, max(8, avail - name_w - 30)).ljust(1), C.GREY_D))
        if badge:
            row += "   " + badge
        lines.append(row)
    while len(lines) < rows - 4:
        lines.append("")
    if st.mode == "input":
        ed = st.editor
        lines.append(pad + paint("Save a channel ", C.RED_HOT, C.BOLD) + paint("❯ ", C.RED_HOT, C.BOLD)
                     + paint(termui.clip(ed.text, avail - 30), C.WHITE, C.BOLD) + paint("▌", C.RED_HOT))
        lines.append(pad + paint("@handle, a channel link, or a name to search for   ·   Enter save   Esc cancel", C.GREY_D))
    elif st.mode == "pick":
        lines.append(pad + paint("Which one?  ", C.AMBER, C.BOLD)
                     + paint("   ".join(f"{i + 1} {termui.clip(r['title'], 22)}" for i, r in enumerate(st.results)),
                             C.OFF))
        lines.append(pad + paint("press a number   ·   Esc cancel", C.GREY_D))
    elif st.mode == "confirm":
        cur = st.current()
        lines.append(pad + paint(f"Remove {termui.clip((cur or {}).get('title', ''), 40)} from your channels?", C.AMBER, C.BOLD))
        lines.append(pad + paint("y remove   ·   any other key keeps it", C.GREY_D))
    else:
        text = (termui.spinner(t) + " " + st.busy) if st.busy else st.message
        lines.append(pad + paint(termui.clip(text, avail - 2), C.AMBER) if text else "")
        lines.append(pad + paint("↑↓ move   Enter open   a add   x remove   n check for new   q back", C.GREY_D))
    return [termui.clip_ansi(ln, avail) for ln in lines[:rows]]


def _new_or_none(cfg, rec, fetcher):
    n, err = new_count(cfg, rec, fetcher)
    return None if err else n


def default_download(cfg):
    """What Enter does inside the explorer: download the picked videos (one, or a planned batch)."""
    def run(picks):
        from .flows import download_one
        from .search import _download_selection
        from .ui import prompt
        if len(picks) == 1:
            download_one(cfg, picks[0]["url"])
        else:
            _download_selection(cfg, picks)
        prompt("  Press enter to return to the channel… ")
    return run


def channels_menu(cfg, on_download=None, reader="auto", out=None, fetcher=_fetch):
    """The My channels screen. Returns when the user leaves."""
    if reader == "auto":
        reader = open_keys()
    if reader is None:
        print(paint("  My channels needs an interactive terminal. Scriptable version:  ytgrab sub list", C.AMBER))
        return
    out = out or sys.stdout
    on_download = on_download or default_download(cfg)
    st = ChannelList()
    done = {}
    lock = threading.Lock()
    t0 = time.monotonic()

    def run_async(key, fn):
        def work():
            try:
                res = fn()
            except Exception as e:                       # never let a worker kill the screen
                res = ("error", str(e))
            with lock:
                done[key] = res
        threading.Thread(target=work, daemon=True).start()

    try:
        with termui.alt_screen(out):
            while True:
                with lock:
                    finished = dict(done)
                    done.clear()
                for key, res in finished.items():
                    if key == "add":
                        ok, msg, _rec = res if isinstance(res, tuple) and len(res) == 3 else (False, str(res[1]), None)
                        st.busy, st.message = "", msg
                        st.reload()
                        if ok:
                            st.cursor = len(st.recs) - 1
                    elif key == "search":
                        st.busy = ""
                        if res and res[0] != "error":
                            st.results, st.mode = res, "pick"
                        else:
                            st.message, st.mode = "No channels found for that name.", "list"
                    elif key[0] == "new":
                        st.new[key[1]] = res if isinstance(res, int) else None
                cols, lines = termui.size()
                termui.draw_full(render_channel_list(st, cols, lines, time.monotonic() - t0), out, lines)
                tok = reader.read(0.08)
                if tok is None:
                    continue
                st.message = ""
                if st.mode == "input":
                    result = st.editor.apply(tok)
                    if result == "escape" or tok == "ESC":
                        st.mode = "list"
                    elif result == "submit" and st.editor.text.strip():
                        text = st.editor.text.strip()
                        st.editor.clear()
                        st.mode = "list"
                        if text.startswith(("@", "http")) or "youtube.com/" in text:
                            st.busy = "Saving channel…"
                            run_async("add", lambda tx=text: add_channel(cfg, tx, fetcher))
                        else:
                            st.busy = f"Searching channels for “{text}”…"
                            run_async("search", lambda tx=text: search_channels(cfg, tx, 6, fetcher) or ("error", ""))
                    continue
                if st.mode == "pick":
                    if tok == "ESC":
                        st.mode = "list"
                    elif tok.isdigit() and 1 <= int(tok) <= len(st.results):
                        url = st.results[int(tok) - 1]["url"]
                        st.mode, st.busy = "list", "Saving channel…"
                        run_async("add", lambda u=url: add_channel(cfg, u, fetcher))
                    continue
                if st.mode == "confirm":
                    if tok == "y" and st.current():
                        ok, msg = remove_channel(st.current()["url"])
                        st.message = msg
                        st.reload()
                    st.mode = "list"
                    continue
                if tok in ("q", "ESC", "BACKSPACE", "CTRL_C", "CTRL_D"):
                    break
                if tok in ("DOWN", "j"):
                    st.move(1)
                elif tok in ("UP", "k"):
                    st.move(-1)
                elif tok == "a":
                    st.mode = "input"
                elif tok in ("x", "DELETE") and st.current():
                    st.mode = "confirm"
                elif tok == "n" and st.recs:
                    for rec in st.recs:
                        st.new[rec["url"]] = "…"
                        run_async(("new", rec["url"]), lambda r=rec: _new_or_none(cfg, r, fetcher))
                elif tok in ("ENTER", "RIGHT") and st.current():
                    explore_channel(cfg, st.current(), on_download, reader=reader, out=out, fetcher=fetcher,
                                    alt=False, close_reader=False)
                    st.reload()
                    st.new.pop(st.current()["url"] if st.current() else "", None)
                elif tok.isdigit() and 1 <= int(tok) <= len(st.recs):
                    st.cursor = int(tok) - 1
    finally:
        try:
            reader.close()
        except Exception:
            pass
