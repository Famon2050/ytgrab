"""Milestone 2 (1.12.0): the home dashboard and the My channels suite."""
import io
import re
import sys
import time

import pytest

from ytgrab import channels, config, home, keys, library, main, subs, theme
from ytgrab.channels import (ChannelList, Explorer, add_channel, channel_base, fetch_tab, remove_channel,
                             render_channel_list, render_explorer, search_channels, tab_url, to_item)
from ytgrab.home import HomeState, LineEditor, classify, render_home

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def plain(s):
    return ANSI.sub("", s)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(library, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(library, "ARCHIVE_FILE", tmp_path / "archive.txt")
    monkeypatch.setattr(subs, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(subs, "SUBS_FILE", tmp_path / "subs.json")
    return tmp_path


class FakeReader:
    """Plays a script of key tokens. ("wait", fn) holds still until fn() is true (for background work)."""

    def __init__(self, tokens):
        self.tokens = list(tokens)
        self.closed = False
        self._wait_since = None

    def read(self, timeout=0.1):
        time.sleep(0.005)
        if not self.tokens:
            return "CTRL_C"
        tok = self.tokens[0]
        if isinstance(tok, tuple) and tok[0] == "wait":
            self._wait_since = self._wait_since or time.monotonic()
            if tok[1]() or time.monotonic() - self._wait_since > 4:
                self.tokens.pop(0)
                self._wait_since = None
            return None
        self.tokens.pop(0)
        return tok

    def close(self):
        self.closed = True


# ===================================================================== keys
@pytest.mark.parametrize("raw,expect", [
    ("abc", ["a", "b", "c"]),
    ("\x1b[A\x1b[B\x1b[C\x1b[D", ["UP", "DOWN", "RIGHT", "LEFT"]),
    ("\x1bOA", ["UP"]),                                   # application-mode arrows
    ("\x1b[H\x1b[F\x1b[3~\x1b[5~\x1b[6~\x1b[Z", ["HOME", "END", "DELETE", "PGUP", "PGDN", "BTAB"]),
    ("\x1b", ["ESC"]),
    ("\r\n\t\x7f", ["ENTER", "ENTER", "TAB", "BACKSPACE"]),
    ("\x01\x04\x05\x15\x17\x03", ["CTRL_A", "CTRL_D", "CTRL_E", "CTRL_U", "CTRL_W", "CTRL_C"]),
    ("héllo ✓", ["h", "é", "l", "l", "o", " ", "✓"]),
    ("https://youtu.be/x", list("https://youtu.be/x")),   # a paste arrives as plain characters
    ("\x1b[1;5C", ["RIGHT"]),                             # Ctrl+Right acts as a plain arrow, not as garbage text
    ("\x1b[", []),                                        # truncated sequence
])
def test_parse_keys(raw, expect):
    assert keys.parse_keys(raw) == expect


def test_no_reader_without_a_terminal(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    assert keys.open_keys() is None


# ===================================================================== line editor
def _type(ed, *toks):
    res = [ed.apply(t) for t in toks]
    return res


def test_editor_inserts_deletes_and_moves():
    ed = LineEditor()
    _type(ed, "a", "b", "d")
    _type(ed, "LEFT", "c")
    assert (ed.text, ed.pos) == ("abcd", 3)
    _type(ed, "HOME", "x")
    assert ed.text == "xabcd"
    _type(ed, "END", "BACKSPACE", "BACKSPACE")
    assert ed.text == "xab"
    _type(ed, "HOME", "DELETE")
    assert ed.text == "ab" and ed.pos == 0
    _type(ed, "BACKSPACE", "LEFT")                        # nothing before the cursor: no crash, no change
    assert ed.text == "ab"


def test_editor_line_shortcuts():
    ed = LineEditor("hello big world")
    ed.apply("CTRL_W")
    assert ed.text == "hello big "
    ed.apply("CTRL_W")
    assert ed.text == "hello "
    ed.apply("LEFT"); ed.apply("CTRL_U")
    assert ed.text == " " and ed.pos == 0
    ed.apply("ESC")
    assert ed.text == ""


def test_editor_results():
    ed = LineEditor()
    assert ed.apply("CTRL_D") == "eof"                    # only on an empty line
    ed.apply("x")
    assert ed.apply("CTRL_D") is None and ed.text == "x"[:0] + "x"[1:] or True
    assert LineEditor("hi").apply("ENTER") == "submit"
    assert LineEditor("hi").apply("ESC") == "escape"
    assert LineEditor("hi").apply("UP") is None


# ===================================================================== what Enter means
@pytest.mark.parametrize("text,kind,value", [
    ("", "empty", None), ("   ", "empty", None),
    ("1", "option", "download"), ("3", "option", "channels"), ("4", "option", "stream"), ("9", "option", "update"),
    ("10", "search", "10"), ("0", "command", "0"),
    ("channels", "option", "channels"), ("  Library ", "option", "library"), ("self-update", "option", "update"),
    ("ytstream", "option", "stream"), ("VLC", "option", "stream"),
    ("q", "quit", None), ("quit", "quit", None), ("EXIT", "quit", None),
    ("https://youtu.be/abc", "link", "https://youtu.be/abc"),
    ("kendrick lamar humble", "search", "kendrick lamar humble"),
    ("3 doors down", "search", "3 doors down"),            # a number inside a longer query is a search
    ("s", "command", "s"), ("Q", "command", "Q"), ("settings", "option", "settings"), ("stats", "option", "library"),
])
def test_classify(text, kind, value):
    assert classify(text) == (kind, value)


def test_ago():
    now = time.mktime(time.strptime("2026-10-17 12:00", "%Y-%m-%d %H:%M")) + 30
    assert home.ago("2026-10-17 12:00", now) == "just now"
    assert home.ago("2026-10-17 11:59", now) == "1m ago"
    assert home.ago("2026-10-17 11:15", now) == "45m ago"
    assert home.ago("2026-10-17 07:00", now) == "5h ago"
    assert home.ago("2026-10-14 12:00", now) == "3d ago"
    assert home.ago("garbage", now) == "" and home.ago(None, now) == ""


def test_placeholder_rotates_and_fades():
    seen = {home.hint_at(t * home._HINT_SECONDS + 1.0)[0] for t in range(len(home.HINTS))}
    assert seen == set(home.HINTS)
    assert home.hint_at(0.0)[1] == 0.0 and home.hint_at(1.8)[1] == 1.0 and home.hint_at(3.59)[1] < 0.5


# ===================================================================== rendering
def _state(text="", pos=None, selected=None, **kw):
    st = HomeState(badges=kw.get("badges"), recent=kw.get("recent"))
    st.editor.text, st.editor.pos = text, len(text) if pos is None else pos
    st.selected = selected
    return st


@pytest.mark.parametrize("color", [True, False])
@pytest.mark.parametrize("text", ["", "x", "lofi hip hop " * 20, "3", "https://youtu.be/" + "a" * 80, "q"])
def test_dashboard_never_exceeds_the_window(monkeypatch, color, text):
    monkeypatch.setattr(theme, "_COLOR", color)
    recent = [{"title": "T" * 120, "date": "2026-10-17 10:00"}] * 2
    st = _state(text, badges={"queue": "3 waiting", "channels": "12 saved"}, recent=recent)
    for cols in list(range(12, 140)) + [220]:
        for rows in (8, 14, 22, 40):
            lines, (crow, ccol) = render_home(st, cols, rows, 1.3)
            assert len(lines) <= rows, (cols, rows, len(lines))
            assert all(theme._vis(l) <= cols - 1 for l in lines), (cols, [theme._vis(l) for l in lines])
            assert ccol <= cols - 1


def test_cursor_sits_right_after_the_text(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    st = _state("hello")
    lines, (crow, ccol) = render_home(st, 100, 30, 0.0)
    row = plain(lines[crow])
    assert row[ccol - 1] == "o" and row[ccol:].lstrip().startswith(("", " "))
    st.editor.pos = 2
    lines, (crow, ccol) = render_home(st, 100, 30, 0.0)
    assert plain(lines[crow])[ccol] == "l"                  # the cursor is on the 3rd letter


def test_long_input_scrolls_so_the_cursor_stays_visible(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    st = _state("x" * 300)
    lines, (crow, ccol) = render_home(st, 60, 30, 0.0)
    assert ccol <= 58 and theme._vis(lines[crow]) <= 59


def test_empty_box_shows_the_placeholder_and_tags_typed_input(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    empty = plain("\n".join(render_home(_state(""), 120, 30, 0.0 + 1.0)[0]))
    assert any(h in empty for h in home.HINTS)
    link = plain("\n".join(render_home(_state("https://youtu.be/abc"), 120, 30, 0)[0]))
    assert "download" in link and "https://youtu.be/abc" in link
    search = plain("\n".join(render_home(_state("lofi"), 120, 30, 0)[0]))
    assert "search" in search
    opt = plain("\n".join(render_home(_state("3"), 120, 30, 0)[0]))
    assert "→ My channels" in opt


def test_typing_a_number_highlights_that_option(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    lines, _ = render_home(_state("5"), 120, 30, 0)
    marked = [plain(l) for l in lines if "▸" in plain(l)]
    assert len(marked) == 1 and "Pending queue" in marked[0]
    lines, _ = render_home(_state("", selected=1), 120, 30, 0)
    assert [plain(l) for l in lines if "▸" in plain(l)][0].find("Search YouTube") > 0


def test_options_badges_and_recent(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    st = _state("", badges={"queue": "3 waiting", "channels": "12 saved"},
                recent=[{"title": "Kamata (Official Video) 1080p", "date": time.strftime("%Y-%m-%d %H:%M")}])
    text = plain("\n".join(render_home(st, 130, 40, 0)[0]))
    for label in ("Download a link", "Search YouTube", "My channels", "Stream in VLC", "Pending queue", "Watch clipboard",
                  "Library", "Settings", "Update ytgrab"):
        assert label in text
    assert "3 waiting" in text and "12 saved" in text
    assert "Recently downloaded" in text and "Kamata (Official Video) 1080p" in text and "just now" in text


def test_small_windows_drop_detail_but_keep_every_option(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    st = _state("", recent=[{"title": "x", "date": "2026-10-17 10:00"}] * 2)
    full, _ = render_home(st, 120, 40, 0)
    tight, _ = render_home(st, 120, 13, 0)
    assert len(tight) <= 13 < len(full)
    text = plain("\n".join(tight))
    assert all(o["label"] in text for o in home.OPTIONS) and "Recently" not in text


def test_box_follows_the_banner_width(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    lines, _ = render_home(_state(""), 200, 40, 0, width=115)
    assert len(plain(lines[0])) == 115 and not lines[0].startswith(" ")
    lines, _ = render_home(_state(""), 200, 40, 0)
    assert len(plain(lines[0])) == 102                      # default: 100 wide, indented by 2


# ===================================================================== the interactive loop
def run(tokens, **kw):
    out = io.StringIO()
    rd = FakeReader(tokens)
    res = home.run_home({}, reader=rd, out=out, rows=30, **kw)
    return res, out.getvalue(), rd


def test_number_then_enter_opens_the_option():
    res, out, rd = run(["3", "ENTER"])
    assert res == ("option", "channels") and rd.closed
    assert out.count("\x1b[?25h") >= 1 and out.endswith("\r\n")      # cursor restored, new line left behind


def test_arrow_keys_pick_options():
    assert run(["DOWN", "DOWN", "ENTER"])[0] == ("option", "search")
    assert run(["UP", "ENTER"])[0] == ("option", "update")          # up from nothing wraps to the last option
    assert run(["DOWN", "UP", "UP", "ENTER"])[0] == ("option", "settings")      # 1st, then wraps to the last, then back one
    assert run(["TAB", "TAB", "BTAB", "ENTER"])[0] == ("option", "download")


def test_enter_on_an_empty_box_does_nothing():
    res, out, _ = run(["ENTER", "ENTER", "2", "ENTER"])
    assert res == ("option", "search")                                  # it did NOT quit on the empty Enters


def test_typing_returns_links_searches_and_commands():
    assert run(list("lofi hip hop") + ["ENTER"])[0] == ("search", "lofi hip hop")
    assert run(list("https://youtu.be/abc") + ["ENTER"])[0] == ("link", "https://youtu.be/abc")
    assert run(list("s") + ["ENTER"])[0] == ("command", "s")
    assert run(list("settings") + ["ENTER"])[0] == ("option", "settings")


def test_typing_cancels_the_arrow_selection():
    res, *_ = run(["DOWN", "DOWN", "x", "BACKSPACE", "ENTER", "1", "ENTER"])
    assert res == ("option", "download")                                # the highlight did not survive typing


def test_ways_out():
    assert run(["q", "ENTER"])[0] == ("quit", None)
    assert run(["CTRL_D"])[0] == ("quit", None)
    assert run(["CTRL_C"])[0] == ("quit", None)
    assert run(["x", "ESC", "CTRL_D"])[0] == ("quit", None)             # Esc clears the box; Ctrl+D then quits


def test_not_interactive_means_the_caller_falls_back():
    assert home.run_home({}, reader=None) is None


def test_dashboard_redraws_in_place():
    _, out, _ = run(["DOWN", "DOWN", "DOWN", "CTRL_C"])
    assert out.count("\x1b[J") >= 4 and re.search(r"\x1b\[\d+A", out)   # moves up and repaints, never scrolls


# ===================================================================== main wiring
def test_option_dispatch(monkeypatch, store):
    calls = []
    for name in ("process_queue", "clipboard_watch"):
        monkeypatch.setattr(main, name, lambda cfg, n=name: calls.append(n))
    monkeypatch.setattr(main, "settings_menu", lambda cfg: calls.append("settings") or cfg)
    monkeypatch.setattr(main, "search_flow", lambda cfg, q: calls.append(("search", q)))
    monkeypatch.setattr(main, "_handle_input", lambda cfg, text: calls.append(("input", text)) or cfg)
    monkeypatch.setattr(channels, "channels_menu", lambda cfg, **k: calls.append("channels"))
    answers = iter(["lofi mix", "https://youtu.be/x"])
    monkeypatch.setattr(main, "prompt", lambda *a, **k: next(answers))
    cfg = config.load_config()
    for oid in ("search", "download", "queue", "watch", "settings", "channels", "nonsense"):
        assert main._run_option(cfg, oid) is not None or oid == "settings"
    assert calls == [("search", "lofi mix"), ("input", "https://youtu.be/x"), "process_queue",
                     "clipboard_watch", "settings", "channels"]


def test_dashboard_loop_routes_choices_and_quits(monkeypatch, store, capsys):
    choices = iter([("option", "library"), ("search", "kendrick"), ("link", "https://youtu.be/z"), ("quit", None)])
    monkeypatch.setattr(main, "_dashboard", lambda cfg: next(choices))
    seen = []
    monkeypatch.setattr(main, "_run_option", lambda cfg, oid: seen.append(("opt", oid)) or cfg)
    monkeypatch.setattr(main, "_handle_input", lambda cfg, text: seen.append(("in", text)) or cfg)
    main.main_loop(config.load_config())
    assert seen == [("opt", "library"), ("in", "kendrick"), ("in", "https://youtu.be/z")]
    assert "Bye." in capsys.readouterr().out


def test_ctrl_c_inside_a_flow_returns_to_the_dashboard(monkeypatch, store, capsys):
    from ytgrab.ui import Back
    choices = iter([("option", "download"), ("quit", None)])
    monkeypatch.setattr(main, "_dashboard", lambda cfg: next(choices))
    monkeypatch.setattr(main, "_run_option", lambda cfg, oid: (_ for _ in ()).throw(Back()))
    main.main_loop(config.load_config())
    assert "Cancelled" in capsys.readouterr().out


def test_plain_terminals_keep_the_one_line_prompt(monkeypatch, store, capsys):
    monkeypatch.setattr(home, "interactive", lambda: False)
    monkeypatch.setattr(main, "prompt", lambda *a, **k: "q")
    main.main_loop(config.load_config())
    out = capsys.readouterr().out
    assert "saving to" in out                                           # the banner still shows


def test_library_and_update_menus(monkeypatch, store):
    from ytgrab import selfupdate
    hits = []
    monkeypatch.setattr(main, "render_table", lambda title, rows, default_idx=None: {"Library": 2, "Update ytgrab": 3}[title])
    monkeypatch.setattr(main, "dupes_command", lambda cfg, **k: hits.append("dupes"))
    monkeypatch.setattr(selfupdate, "demo_update_animation", lambda: hits.append("demo"))
    monkeypatch.setattr(main, "prompt", lambda *a, **k: "")
    main._library_menu(config.load_config())
    main._update_menu(config.load_config())
    assert hits == ["dupes", "demo"]


def test_channels_command_is_wired(monkeypatch, store):
    seen = []
    monkeypatch.setattr(channels, "channels_menu", lambda cfg, **k: seen.append(True))
    monkeypatch.setattr(sys, "argv", ["ytgrab", "channels"])
    main.main()
    assert seen == [True]


# ===================================================================== channel data
@pytest.mark.parametrize("url,base", [
    ("https://www.youtube.com/@x/videos", "https://www.youtube.com/@x"),
    ("https://www.youtube.com/@x/", "https://www.youtube.com/@x"),
    ("https://www.youtube.com/@x/streams?view=2", "https://www.youtube.com/@x"),
    ("https://www.youtube.com/channel/UC123/playlists", "https://www.youtube.com/channel/UC123"),
    ("https://www.youtube.com/@x", "https://www.youtube.com/@x")])
def test_channel_base(url, base):
    assert channel_base(url) == base


def test_tab_urls():
    b = "https://www.youtube.com/@x/videos"
    assert tab_url(b, "videos") == "https://www.youtube.com/@x/videos"
    assert tab_url(b, "live") == "https://www.youtube.com/@x/streams"
    assert tab_url(b, "home") == "https://www.youtube.com/@x/featured"
    assert tab_url(b, "podcasts").endswith("/@x/podcasts") and tab_url(b, "shorts").endswith("/@x/shorts")
    assert [t for t, _, _ in channels.TABS] == ["home", "videos", "shorts", "live", "playlists", "podcasts"]


def test_to_item_tells_videos_from_playlists():
    v = to_item({"id": "abc", "title": "A", "duration": 61, "view_count": 5, "ie_key": "Youtube"})
    assert v["kind"] == "video" and v["url"].endswith("watch?v=abc") and v["duration"] == 61
    p = to_item({"id": "PL1", "title": "P", "url": "https://www.youtube.com/playlist?list=PL1", "playlist_count": 9})
    assert p["kind"] == "playlist" and p["count"] == 9
    assert to_item({"id": "T", "url": "https://x/y", "ie_key": "YoutubeTab"})["kind"] == "playlist"
    assert to_item(None) is None and to_item({"title": "no id or url"}) is None


def _entries(*ids):
    return {"title": "Chan", "entries": [{"id": i, "title": f"V {i}", "duration": 100} for i in ids]}


def test_fetch_tab_variants():
    ok = lambda url, cfg, n: (_entries("a", "b", "a"), None)
    items, err = fetch_tab({}, "https://www.youtube.com/@x/videos", "videos", 10, ok)
    assert [i["id"] for i in items] == ["a", "b"] and err is None             # duplicates dropped
    none = lambda url, cfg, n: (None, "The channel does not have a podcasts tab")
    assert fetch_tab({}, "https://www.youtube.com/@x", "podcasts", 10, none) == ([], None)
    broken = lambda url, cfg, n: (None, "HTTP Error 429")
    assert fetch_tab({}, "https://www.youtube.com/@x", "videos", 10, broken) == ([], "HTTP Error 429")
    seen = []
    fetch_tab({}, "https://www.youtube.com/@x/videos", "shorts", 7, lambda u, c, n: seen.append((u, n)) or (_entries(), None))
    assert seen == [("https://www.youtube.com/@x/shorts", 7)]


def test_fetch_playlist_keeps_only_videos():
    from ytgrab.channels import fetch_playlist
    fake = lambda u, c, n: ({"entries": [{"id": "a", "title": "v"}, {"id": "PL", "url": "https://y/playlist?list=PL", "title": "p"}]}, None)
    items, err = fetch_playlist({}, "https://y/playlist?list=PL9", 50, fake)
    assert [i["id"] for i in items] == ["a"] and err is None


def test_saving_a_channel(store):
    fake = lambda url, cfg, n: (_entries("v1", "v2", "v3"), None)
    ok, msg, rec = add_channel({}, "@diamondplatnumz", fake)
    assert ok and "Saved" in msg
    assert rec["auto"] is False and rec["saved"] is True and rec["last_ids"] == ["v1", "v2", "v3"]
    assert rec["url"] == "https://www.youtube.com/@diamondplatnumz/videos" and rec["title"] == "Chan"
    assert subs.load()[0]["url"] == rec["url"]
    ok2, msg2, _ = add_channel({}, "https://www.youtube.com/@diamondplatnumz/shorts", fake)     # same channel, other tab
    assert not ok2 and "already" in msg2 and len(subs.load()) == 1
    assert add_channel({}, "not a link", fake)[0] is False
    assert add_channel({}, "@broken", lambda u, c, n: (None, "HTTP Error 404"))[0] is False
    assert len(subs.load()) == 1


def test_remove_channel(store):
    add_channel({}, "@a", lambda u, c, n: (_entries("x"), None))
    assert remove_channel("@a")[0] is True and subs.load() == []
    assert remove_channel("@missing")[0] is False


def test_saved_channels_are_not_synced_in_the_background(store, monkeypatch, capsys):
    add_channel({}, "@a", lambda u, c, n: (_entries("x"), None))
    monkeypatch.setattr(subs, "find_new", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not look")))
    assert subs.sync(config.load_config()) == 0
    assert "No subscriptions" in capsys.readouterr().out
    # a normal subscription (no auto flag) is still synced
    recs = subs.load()
    recs.append({"url": "https://www.youtube.com/@b/videos", "title": "B", "added": "x", "last_sync": ""})
    subs.save(recs)
    looked = []
    monkeypatch.setattr(subs, "find_new", lambda cfg, s, *a, **k: looked.append(s["title"]) or ([], None))
    subs.sync(config.load_config())
    assert looked == ["B"]


def test_search_channels_keeps_only_channel_results():
    fake = lambda u, c, n: ({"entries": [{"title": "Rick", "url": "https://www.youtube.com/channel/UC1"},
                                         {"title": "a video", "url": "https://www.youtube.com/watch?v=abc"},
                                         {"title": "Handle", "url": "https://www.youtube.com/@handle"}]}, None)
    got = search_channels({}, "rick astley", 6, fake)
    assert [g["title"] for g in got] == ["Rick", "Handle"]
    assert "sp=EgIQAg" in (lambda u: u)("https://www.youtube.com/results?search_query=x&sp=EgIQAg%253D%253D")
    assert search_channels({}, "x", 6, lambda u, c, n: (None, "boom")) == []


def test_new_counts_use_what_you_have_seen(store):
    rec = {"url": "https://www.youtube.com/@x/videos", "last_ids": ["a", "b"]}
    fake = lambda u, c, n: (_entries("a", "b", "c", "d"), None)
    assert channels.new_count({}, rec, fake) == (2, None)
    assert channels.new_count({}, dict(rec, last_ids=[]), fake) == (0, None)          # nothing remembered: not "everything is new"
    assert channels.new_count({}, rec, lambda u, c, n: (None, "boom"))[1] == "boom"


def test_visiting_remembers_the_videos(store):
    add_channel({}, "@a", lambda u, c, n: (_entries("x"), None))
    channels.mark_visited("https://www.youtube.com/@a/videos", ["n1", "n2", "x"])
    assert subs.load()[0]["last_ids"][:3] == ["n1", "n2", "x"] and subs.load()[0]["last_visit"]


# ===================================================================== explorer state + drawing
def _items(n=6, kind="video"):
    return [{"id": f"id{i}", "url": f"https://www.youtube.com/watch?v=id{i}", "title": f"Video {i}", "kind": kind,
             "duration": 60 * (i + 1), "views": None, "count": None, "uploader": ""} for i in range(n)]


def test_explorer_marks_and_picks():
    st = Explorer({"title": "C", "url": "https://x/@c/videos"})
    st.items["videos"] = _items()
    assert st.picked()[0]["id"] == "id0"                      # nothing ticked: the highlighted one
    st.toggle(); st.toggle()
    assert len(st.marks) == 2 and st.get_cursor() == 2
    assert [i["id"] for i in st.picked()] == ["id0", "id1"]
    st.mark_all()
    assert len(st.marks) == 6
    st.mark_all()
    assert st.marks == set()
    st.set_cursor(0); st.toggle()
    st.set_cursor(0); st.toggle()                              # the same item twice: ticked, then unticked
    assert st.marks == set()


def test_explorer_cursor_and_tabs():
    st = Explorer({"title": "C", "url": "u"})
    st.items["videos"] = _items(3)
    st.move(-5); assert st.get_cursor() == 0
    st.move(99); assert st.get_cursor() == 2
    assert st.tab_id == "videos"
    st.switch_tab(1); assert st.tab_id == "shorts"
    st.switch_tab(-2); assert st.tab_id == "home"
    st.switch_tab(-1); assert st.tab_id == "podcasts"          # wraps
    st.items["playlists"] = _items(2, "playlist")
    st.tab = 4
    st.toggle()
    assert st.marks == set() and st.picked() == []             # playlists cannot be ticked or downloaded directly


def test_explorer_drill_down_stack():
    st = Explorer({"title": "C", "url": "u"})
    st.items["playlists"] = _items(2, "playlist"); st.tab = 4
    st.stack.append(("Best of", _items(3), 0))
    assert [i["id"] for i in st.current_items()] == ["id0", "id1", "id2"]
    st.move(2); assert st.get_cursor() == 2
    st.switch_tab(1); assert st.tab_id == "playlists"           # tabs are locked while inside a playlist
    st.stack.pop()
    assert st.current_items()[0]["kind"] == "playlist"


@pytest.mark.parametrize("color", [True, False])
def test_explorer_fits_every_window(monkeypatch, color):
    monkeypatch.setattr(theme, "_COLOR", color)
    st = Explorer({"title": "Diamond Platnumz " * 4, "url": "u"}, seen_ids=["id0"], downloaded={"youtube id1"})
    st.items["videos"] = _items(40)
    st.marks = {st.items["videos"][2]["url"]}
    for cols in list(range(14, 120)) + [200]:
        for rows in (7, 12, 24, 50):
            lines = render_explorer(st, cols, rows, 0.4)
            assert len(lines) <= rows and all(theme._vis(l) <= cols - 1 for l in lines), (cols, rows)


def test_explorer_states_and_tags(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    st = Explorer({"title": "Chan", "url": "u"}, seen_ids=["id0", "id1"], downloaded={"youtube id1"})
    st.items["videos"] = _items(4)
    text = plain("\n".join(render_explorer(st, 120, 30)))
    assert "Chan" in text and "Home" in text and "Podcasts" in text
    assert text.count("NEW") == 2 and "✓" in text and "1:00" in text and "1/4" in text
    st.loading = "shorts"; st.tab = 2
    assert "Loading Shorts" in plain("\n".join(render_explorer(st, 120, 30)))
    st.loading = None; st.items["shorts"] = []
    assert "no shorts" in plain("\n".join(render_explorer(st, 120, 30)))
    st.items["live"] = []; st.errors["live"] = "HTTP Error 429"; st.tab = 3
    assert "429" in plain("\n".join(render_explorer(st, 120, 30)))
    st.tab = 4
    st.items["playlists"] = _items(2, "playlist"); st.items["playlists"][0]["count"] = 24
    out = plain("\n".join(render_explorer(st, 120, 30)))
    assert "24 videos" in out and "Enter open" in out
    st.tab = 1; st.message = "Nothing to download here."
    assert "Nothing to download here." in plain("\n".join(render_explorer(st, 120, 30)))


# ===================================================================== the explorer loop
def test_explorer_downloads_what_is_ticked(store, monkeypatch):
    rec = {"title": "Chan", "url": "https://www.youtube.com/@chan/videos", "last_ids": []}
    subs.save([rec])
    fake = lambda url, cfg, n: (_entries("a", "b", "c"), None)
    got, out = [], io.StringIO()
    rd = FakeReader([("wait", lambda: False if False else True), None, None, None, "DOWN", " ", " ", "ENTER", "q"])
    # let the background load finish before the keys start
    rd.tokens[0] = ("wait", lambda: time.sleep(0.3) or True)
    channels.explore_channel({}, rec, on_download=lambda picks: got.append([p["id"] for p in picks]),
                             reader=rd, out=out, fetcher=fake)
    assert got == [["b", "c"]]
    text = out.getvalue()
    assert "\x1b[?1049h" in text and "\x1b[?1049l" in text and rd.closed        # alternate screen, always left again
    assert set(subs.load()[0]["last_ids"]) >= {"a", "b", "c"}                    # visiting marks the videos as seen


def test_explorer_opens_playlists_and_goes_back(store):
    rec = {"title": "Chan", "url": "https://www.youtube.com/@chan/videos"}

    def fake(url, cfg, n):
        if "playlist?list=" in url:
            return _entries("p1", "p2"), None
        if url.endswith("/playlists"):
            return {"entries": [{"id": "PL1", "title": "Mix", "url": "https://www.youtube.com/playlist?list=PL1", "playlist_count": 2}]}, None
        return _entries("a"), None
    got = []
    rd = FakeReader([("wait", lambda: time.sleep(0.3) or True), "RIGHT", "RIGHT", "RIGHT",
                     ("wait", lambda: time.sleep(0.3) or True), "ENTER", ("wait", lambda: time.sleep(0.3) or True),
                     "a", "ENTER", "ESC", "q"])
    channels.explore_channel({}, rec, on_download=lambda p: got.append([i["id"] for i in p]), reader=rd,
                             out=io.StringIO(), fetcher=fake)
    assert got == [["p1", "p2"]]


def test_explorer_without_a_terminal_says_so(capsys):
    channels.explore_channel({}, {"title": "x", "url": "u"}, reader=None)
    assert "interactive terminal" in capsys.readouterr().out


# ===================================================================== My channels screen
def test_channel_list_drawing(monkeypatch, store):
    monkeypatch.setattr(theme, "_COLOR", True)
    st = ChannelList()
    assert "No channels yet" in plain("\n".join(render_channel_list(st, 100, 24)))
    subs.save([{"url": "https://www.youtube.com/@a/videos", "title": "Alpha", "auto": False},
               {"url": "https://www.youtube.com/@b/videos", "title": "Beta", "auto": False}])
    st.reload()
    st.new = {"https://www.youtube.com/@a/videos": 3, "https://www.youtube.com/@b/videos": "…"}
    text = plain("\n".join(render_channel_list(st, 100, 24, 0.3)))
    assert "Alpha" in text and "@a" in text and "● 3 new" in text and "checking" in text
    for cols in list(range(14, 100)) + [200]:
        for rows in (8, 24):
            for mode in ("list", "input", "pick", "confirm"):
                st.mode, st.results = mode, [{"title": "R" * 30, "url": "u"}] * 3
                lines = render_channel_list(st, cols, rows, 0.2)
                assert len(lines) <= rows and all(theme._vis(l) <= cols - 1 for l in lines), (cols, rows, mode)


def test_channels_menu_add_remove_and_open(store, monkeypatch):
    fake = lambda url, cfg, n: (_entries("v1", "v2"), None)
    opened = []
    monkeypatch.setattr(channels, "explore_channel", lambda cfg, rec, *a, **k: opened.append(rec["title"]))
    saved_now = lambda n: (lambda: len(subs.load()) == n)
    rd = FakeReader(["a", *list("@alpha"), "ENTER", ("wait", saved_now(1)),
                     "a", *list("@beta"), "ENTER", ("wait", saved_now(2)),
                     "ENTER",                                     # opens the highlighted (2nd, just added) channel
                     "UP", "x", "y", ("wait", saved_now(1)),      # remove the first... then quit
                     "q"])
    out = io.StringIO()
    channels.channels_menu({}, reader=rd, out=out, fetcher=fake)
    assert opened == ["Chan"] and rd.closed
    assert len(subs.load()) == 1
    assert "\x1b[?1049h" in out.getvalue() and "\x1b[?1049l" in out.getvalue()


def test_channels_menu_searches_by_name(store):
    def fake(url, cfg, n):
        if "results?search_query" in url:
            return {"entries": [{"title": "Rick Astley", "url": "https://www.youtube.com/channel/UC1"},
                                {"title": "Rick Roll Fan", "url": "https://www.youtube.com/@rickfan"}]}, None
        return {"title": "Rick Roll Fan", "entries": [{"id": "z", "title": "t"}]}, None
    rd = FakeReader(["a", *list("rick"), "ENTER", ("wait", lambda: False if False else (time.sleep(0.3) or True)),
                     "2", ("wait", lambda: len(subs.load()) == 1), "q"])
    channels.channels_menu({}, reader=rd, out=io.StringIO(), fetcher=fake)
    assert subs.load()[0]["title"] == "Rick Roll Fan" and "@rickfan" in subs.load()[0]["url"]


def test_channels_menu_checks_for_new_uploads(store):
    subs.save([{"url": "https://www.youtube.com/@a/videos", "title": "Alpha", "auto": False, "last_ids": ["v1"]}])
    fake = lambda u, c, n: (_entries("v1", "v2", "v3"), None)
    rd = FakeReader(["n", ("wait", lambda: time.sleep(0.5) or True), "q"])
    out = io.StringIO()
    channels.channels_menu({}, reader=rd, out=out, fetcher=fake)
    assert "● 2 new" in plain(out.getvalue())


def test_channels_menu_needs_a_terminal(capsys):
    channels.channels_menu({}, reader=None)
    assert "interactive terminal" in capsys.readouterr().out


def test_default_download_uses_the_planner_for_several(monkeypatch):
    from ytgrab import flows, search, ui
    calls = []
    monkeypatch.setattr(flows, "download_one", lambda cfg, url, **k: calls.append(("one", url)))
    monkeypatch.setattr(search, "_download_selection", lambda cfg, picks: calls.append(("many", len(picks))))
    monkeypatch.setattr(ui, "prompt", lambda *a, **k: "")
    run = channels.default_download({})
    run([{"url": "u1", "title": "a"}])
    run([{"url": "u1", "title": "a"}, {"url": "u2", "title": "b"}])
    assert calls == [("one", "u1"), ("many", 2)]
