"""Headless tests for the Textual UI (drives the real app with Pilot)."""
import asyncio
import http.server
import json
import threading
import time
import wave

import pytest

pytest.importorskip("textual")

from textual.widgets import DataTable, Input, Select, Static, TabbedContent  # noqa: E402

from ytgrab import config, library, pending, subs, tui  # noqa: E402


# ---- fixtures --------------------------------------------------------------
@pytest.fixture
def env(tmp_path, monkeypatch):
    cdir = tmp_path / "cfg"; cdir.mkdir()
    for mod in (config, library, pending, subs):
        monkeypatch.setattr(mod, "CONFIG_DIR", cdir, raising=False)
    monkeypatch.setattr(config, "CONFIG_FILE", cdir / "config.json")
    monkeypatch.setattr(config, "HISTORY_FILE", cdir / "history.json")
    monkeypatch.setattr(library, "ARCHIVE_FILE", cdir / "archive.txt")
    monkeypatch.setattr(pending, "QUEUE_FILE", cdir / "queue.json")
    monkeypatch.setattr(subs, "SUBS_FILE", cdir / "subs.json")
    monkeypatch.setattr(tui, "save_config", lambda cfg: None)
    dl = tmp_path / "dl"; dl.mkdir()
    cfg = dict(config.DEFAULTS, download_dir=str(dl), cookies_source="none",
               notify=False, last_video_quality="best", profiles={})
    return cfg, tmp_path, dl


@pytest.fixture
def server(tmp_path):
    """Local HTTP server with a quick file and a slow (throttled) file."""
    root = tmp_path / "srv"; root.mkdir()
    for name, secs in (("fast", 1), ("slow", 3)):
        with wave.open(str(root / f"{name}.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes(b"\x01\x02" * 8000 * secs)

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k): super().__init__(*a, directory=str(root), **k)
        def log_message(self, *a): pass
        def copyfile(self, src, dst):
            slow = self.path.startswith("/slow")
            while chunk := src.read(4096):
                try: dst.write(chunk)
                except OSError: return
                if slow: time.sleep(0.03)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def wait_for(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.1)
    return False


def run(coro):
    return asyncio.run(coro)


# ---- tests -----------------------------------------------------------------
def test_mounts_with_all_tabs(env):
    cfg, *_ = env
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert app.query_one(TabbedContent).active == "tab-home"
            for tid in ("#home-channels", "#home-recent", "#dl", "#results", "#pend", "#subs", "#hist"):
                app.query_one(tid, DataTable)
            assert app.query_one("#kind", Select).value == "video"
            await pilot.press("5"); await pilot.pause()
            assert app.query_one("#url", Input).value == "5"     # typed into the box
            await pilot.press("escape"); await pilot.press("5"); await pilot.pause()
            assert app.query_one(TabbedContent).active == "tab-hist"
    run(go())


def test_kind_switch_changes_quality_options(env):
    cfg, *_ = env
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(140, 40)) as pilot:
            app.set_focus(None)
            await pilot.press("m"); await pilot.pause()
            assert app.query_one("#kind", Select).value == "audio"
            assert app.query_one("#quality", Select).value in ("mp3", "m4a", "opus", "wav")
            assert app.cfg["last_kind"] == "audio"
    run(go())


def test_theme_cycles(env):
    cfg, *_ = env
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(140, 40)) as pilot:
            app.set_focus(None)
            before = app.theme
            await pilot.press("t"); await pilot.pause()
            assert app.theme != before and app.cfg["theme"] == "nord"
    run(go())


def test_download_end_to_end(env, server):
    """Paste a URL -> job runs -> file saved, history + archive written."""
    cfg, _, dl = env
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(140, 40)) as pilot:
            inp = app.query_one("#url", Input)
            inp.value = f"{server}/fast.wav"
            await pilot.press("enter")
            assert await wait_for(lambda: app.jobs and app.jobs[0].status == "done")
            await pilot.pause(0.5)
            assert (dl / "fast.wav").exists()
            assert app.jobs[0].size > 0
            assert len(config.load_history()) == 1
            assert library.archive_set()
            row = app.query_one("#dl", DataTable).get_row_at(0)
            assert "done" in str(row[3]) and "100%" in str(row[4])
            await pilot.press("escape"); await pilot.press("5"); await pilot.pause()
            assert app.query_one("#hist", DataTable).row_count == 1
    run(go())


def test_pause_resume_cancel(env, server):
    cfg, *_ = env
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(140, 40)) as pilot:
            app.query_one("#url", Input).value = f"{server}/slow.wav"
            await pilot.press("enter")
            assert await wait_for(lambda: app.jobs and app.jobs[0].status == "downloading")
            assert await wait_for(lambda: app.jobs[0].speed)
            assert "\x1b" not in app.jobs[0].speed + app.jobs[0].eta       # no raw ANSI codes
            assert app.jobs[0].speed.endswith("/s")
            app.set_focus(app.query_one("#dl", DataTable))
            await pilot.press("p"); await pilot.pause()
            assert app.jobs[0].status == "paused"
            frozen = app.jobs[0].pct
            await asyncio.sleep(0.6)
            assert app.jobs[0].pct == frozen                  # really stopped
            await pilot.press("p")
            assert await wait_for(lambda: app.jobs[0].status in ("downloading", "processing", "done"))
            await pilot.press("c")                            # cancel while running
            assert await wait_for(lambda: app.jobs[0].status in ("cancelled", "done"))
    run(go())


def test_error_retry_and_clear(env, server):
    cfg, *_ = env
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(140, 40)) as pilot:
            # bad URL typed in: info fetch fails -> error toast, no job queued
            app.query_one("#url", Input).value = f"{server}/missing.wav"
            await pilot.press("enter"); await pilot.pause(1.5)
            assert not app.jobs
            # a job that fails while downloading -> error, retry, remove
            app._enqueue(tui.make_job(cfg, "video", "best", f"{server}/missing.wav", "missing"))
            assert await wait_for(lambda: app.jobs and app.jobs[0].status == "error")
            assert app.jobs[0].error
            app.set_focus(app.query_one("#dl", DataTable))
            await pilot.press("r")
            assert await wait_for(lambda: app.jobs[0].status == "error")   # retried, fails again
            await pilot.press("delete"); await pilot.pause()
            assert app.query_one("#dl", DataTable).row_count == 0 and not app.jobs
    run(go())


def test_search_filter_and_queue(env, monkeypatch):
    cfg, *_ = env
    results = [{"title": "[Official Video] Short", "url": "https://youtu.be/a", "id": "a",
                "uploader": "Chan", "duration": 120, "views": 1500},
               {"title": "Long one", "url": "https://youtu.be/b", "id": "b",
                "uploader": "Chan", "duration": 1800, "views": 2_000_000}]
    monkeypatch.setattr(tui, "search_youtube", lambda q, n=20, cfg=None, quiet=False: results)
    queued = []
    async def go():
        app = tui.YtGrabApp(cfg)
        monkeypatch.setattr(app, "_enqueue", lambda job: queued.append(job))
        async with app.run_test(size=(140, 40)) as pilot:
            app.query_one("#url", Input).value = "lofi beats"
            await pilot.press("enter")
            assert await wait_for(lambda: app.query_one(TabbedContent).active == "tab-search")
            t = app.query_one("#results", DataTable)
            assert t.row_count == 2                            # '[...]' title rendered, no crash
            f = app.query_one("#lenfilter", Input)
            f.value = ">10"; app.set_focus(f); await pilot.press("enter"); await pilot.pause()
            assert t.row_count == 1
            app.set_focus(t); await pilot.press("enter"); await pilot.pause()
            assert [j.url for j in queued] == ["https://youtu.be/b"]
    run(go())


def test_playlist_picker_skips_archived(env, monkeypatch):
    cfg, _, dl = env
    library.archive_add(["youtube e2"])
    entries = [{"id": f"e{i}", "title": f"Vid {i} [HD]", "url": f"https://youtu.be/e{i}",
                "index": i, "duration": 60} for i in (1, 2, 3)]
    info = {"_type": "playlist", "title": "My List", "entries": entries}
    monkeypatch.setattr(tui, "fetch_info_quiet", lambda url, cfg: (info, None))
    queued = []
    async def go():
        app = tui.YtGrabApp(cfg)
        monkeypatch.setattr(app, "_enqueue", lambda job: queued.append(job))
        async with app.run_test(size=(140, 50)) as pilot:
            app.query_one("#url", Input).value = "https://www.youtube.com/playlist?list=PL1"
            await pilot.press("enter")
            assert await wait_for(lambda: isinstance(app.screen, tui.PlaylistScreen))
            await pilot.pause()
            await pilot.click("#pl-ok"); await pilot.pause()
            assert await wait_for(lambda: len(queued) == 2)
            assert [j.url[-2:] for j in queued] == ["e1", "e3"]          # e2 was archived
            assert all("My List" in j.opts["outtmpl"] and "001 - " in j.opts["outtmpl"]
                       or "003 - " in j.opts["outtmpl"] for j in queued)
    run(go())


def test_channel_prompt_subscribes(env, monkeypatch):
    cfg, *_ = env
    seen_args = {}
    def fake_sub(cfg, text, mark_seen):
        seen_args.update(text=text, mark_seen=mark_seen)
        return True, "Subscribed to X", "X", []
    monkeypatch.setattr(tui.subs, "subscribe", fake_sub)
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(140, 40)) as pilot:
            app.query_one("#url", Input).value = "@somechannel"
            await pilot.press("enter")
            assert await wait_for(lambda: isinstance(app.screen, tui.ChoiceScreen))
            await pilot.pause()
            await pilot.click("#c-sub")
            assert await wait_for(lambda: seen_args)
            assert seen_args == {"text": "@somechannel", "mark_seen": True}
    run(go())


def test_quit_confirms_when_downloading(env, server):
    cfg, *_ = env
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(140, 40)) as pilot:
            app.query_one("#url", Input).value = f"{server}/slow.wav"
            await pilot.press("enter")
            assert await wait_for(lambda: app.jobs and app.jobs[0].status == "downloading")
            await pilot.press("ctrl+q")
            assert await wait_for(lambda: isinstance(app.screen, tui.ChoiceScreen))
            await pilot.pause()
            await pilot.click("#c-quit")
            assert await wait_for(lambda: app.jobs[0]._cancel.is_set())
    run(go())


def test_dialog_can_stay_open_while_downloads_progress(env, server):
    """A dialog can stay open for as long as you like while downloads keep updating."""
    cfg, *_ = env
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(140, 40)) as pilot:
            app.query_one("#url", Input).value = f"{server}/slow.wav"
            await pilot.press("enter")
            assert await wait_for(lambda: app.jobs and app.jobs[0].status == "downloading")
            app.push_screen(tui.ChoiceScreen("Take your time", [("ok", "OK", "success")]))
            await pilot.pause()
            await asyncio.sleep(1.5)                              # several refresh ticks
            assert isinstance(app.screen, tui.ChoiceScreen)       # still alive, dialog still up
            await pilot.press("p")                                # app-level key while a dialog is open
            await pilot.click("#c-ok"); await pilot.pause()
            assert app.is_running
            app.runner.shutdown(app.jobs)
    run(go())


def test_refresh_survives_shutdown(env):
    """Regression: the refresh timer fired during teardown and raised NoMatches
    (intermittent failure of the suite, and a stack trace when quitting)."""
    cfg, *_ = env
    async def go():
        for _ in range(6):
            app = tui.YtGrabApp(cfg)
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause(0.05)
            app._tick()                                  # after exit: must be a harmless no-op
            app._show_results("x", [])
            app._refresh_history()
    run(go())


def test_tui_sync_uses_filters_formats_and_logs_failures(env, monkeypatch):
    """The TUI's sync must behave like `ytgrab sync`: filters, per-subscription format, seen-marking."""
    cfg, *_ = env
    subs.save([{"url": "chan", "title": "Pods", "last_sync": "", "kind": "audio", "quality": "mp3",
                "min_minutes": 5, "exclude": "trailer"}])
    es = [{"id": "e1", "title": "Episode 1", "duration": 1800, "url": "https://youtu.be/e1"},
          {"id": "e2", "title": "Trailer", "duration": 1800, "url": "https://youtu.be/e2"},
          {"id": "e3", "title": "tiny", "duration": 30, "url": "https://youtu.be/e3"}]
    monkeypatch.setattr(subs, "fetch_recent", lambda url, cfg, n=15: ({"entries": es}, None))
    queued = []
    async def go():
        app = tui.YtGrabApp(cfg)
        monkeypatch.setattr(app, "_enqueue", lambda job: queued.append(job))
        async with app.run_test(size=(140, 40)) as pilot:
            app.action_sync()
            assert await wait_for(lambda: queued)
            await pilot.pause()
    run(go())
    assert [(j.url, j.kind, j.quality) for j in queued] == [("https://youtu.be/e1", "audio", "mp3")]
    assert {"youtube e2", "youtube e3"} <= library.archive_set()          # filtered out -> remembered
    assert subs.load()[0]["last_sync"]


def test_second_download_of_the_same_video_asks_first(env, server):
    """Paste the same URL again -> told it is already there, with the location."""
    from ytgrab.tui import ChoiceScreen
    cfg, _, dl = env
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(140, 40)) as pilot:
            inp = app.query_one("#url", Input)
            inp.value = f"{server}/fast.wav"
            await pilot.press("enter")
            assert await wait_for(lambda: app.jobs and app.jobs[0].status == "done")
            await pilot.pause(0.5)
            assert config.load_history()[0]["key"] == "generic fast"
            # same video again
            inp.value = f"{server}/fast.wav"
            await pilot.press("enter")
            assert await wait_for(lambda: isinstance(app.screen, ChoiceScreen))
            assert str(dl / "fast.wav") in app.screen._message
            await pilot.press("escape"); await pilot.pause()          # Skip
            assert len(app.jobs) == 1
            # ...and "Download again" really does queue it
            inp.value = f"{server}/fast.wav"
            await pilot.press("enter")
            assert await wait_for(lambda: isinstance(app.screen, ChoiceScreen))
            await pilot.click("#c-again"); await pilot.pause()
            assert await wait_for(lambda: len(app.jobs) == 2 and app.jobs[1].status == "done")
            await pilot.pause(0.5)
    run(go())


def test_search_results_can_be_ticked_and_queued_together(env, monkeypatch):
    cfg, _, dl = env
    results = [{"title": f"Vid {c}", "url": f"https://youtu.be/{c}", "id": c, "uploader": "Chan",
                "duration": 100 + i, "views": 10} for i, c in enumerate("abc")]
    have = dl / "Vid c.mp4"; have.write_bytes(b"x")                    # "c" is already downloaded
    (config.CONFIG_DIR / "history.json").write_text(json.dumps(
        [{"title": "Vid c", "file": str(have), "key": "youtube c", "url": "https://youtu.be/c"}]))
    monkeypatch.setattr(tui, "search_youtube", lambda q, n=20, cfg=None, quiet=False: results)
    queued = []
    async def go():
        app = tui.YtGrabApp(cfg)
        monkeypatch.setattr(app, "_enqueue", lambda job: queued.append(job))
        async with app.run_test(size=(140, 40)) as pilot:
            app.query_one("#url", Input).value = "lofi"
            await pilot.press("enter")
            assert await wait_for(lambda: app.query_one(TabbedContent).active == "tab-search")
            t = app.query_one("#results", DataTable)
            app.set_focus(t); await pilot.pause()
            await pilot.press("space"); await pilot.press("space"); await pilot.pause()
            assert app._marked == {"https://youtu.be/a", "https://youtu.be/b"}
            assert t.cursor_row == 2                                   # stepped down as it marked
            await pilot.press("a"); await pilot.pause()                # mark all
            assert len(app._marked) == 3 and "3 marked" in app.sub_title
            await pilot.press("a"); await pilot.pause()                # ...and clear
            assert app._marked == set()
            await pilot.press("up"); await pilot.press("up")
            await pilot.press("space"); await pilot.press("space"); await pilot.press("space")
            assert len(app._marked) == 3
            await pilot.press("enter"); await pilot.pause()
            assert sorted(j.url for j in queued) == ["https://youtu.be/a", "https://youtu.be/b"]   # "c" skipped
            assert app._marked == set()
    run(go())



def test_playlist_select_all_does_not_select_archived(env):
    cfg, *_ = env
    entries = [{"id": f"p{i}", "title": f"Vid {i}", "url": f"https://youtu.be/p{i}",
                "index": i, "duration": 60} for i in (1, 2)]
    result = []
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(140, 50)) as pilot:
            app.push_screen(tui.PlaylistScreen("List", entries, {1}), result.append)
            await pilot.pause()
            await pilot.click("#pl-all")
            await pilot.click("#pl-ok")
            await pilot.pause()
            assert result == [[0]]
    run(go())


def test_single_search_pick_skips_archived_video(env, monkeypatch):
    cfg, *_ = env
    library.archive_add(["youtube archived"])
    results = [{"title": "Already archived", "url": "https://youtu.be/archived",
                "id": "archived", "uploader": "Chan", "duration": 60}]
    monkeypatch.setattr(tui, "search_youtube", lambda q, n=20, cfg=None, quiet=False: results)
    queued = []
    async def go():
        app = tui.YtGrabApp(cfg)
        monkeypatch.setattr(app, "_enqueue", lambda job: queued.append(job))
        async with app.run_test(size=(140, 40)) as pilot:
            app.query_one("#url", Input).value = "some search"
            await pilot.press("enter")
            assert await wait_for(lambda: app.query_one(TabbedContent).active == "tab-search")
            t = app.query_one("#results", DataTable)
            app.set_focus(t)
            await pilot.press("enter")
            await pilot.pause()
            assert queued == []
    run(go())


def test_home_dashboard_shows_saved_creators_and_recent_downloads(env):
    cfg, *_ = env
    from ytgrab import subs
    subs.save([{"title": "My Creator", "url": "https://www.youtube.com/@creator/videos", "last_sync": "2026-10-17"}])
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(140, 40)):
            assert app.query_one("#tabs", TabbedContent).active == "tab-home"
            assert app.query_one("#home-channels", DataTable).row_count == 1
            assert "My Creator" in str(app.query_one("#home-channels", DataTable).get_row_at(0)[0])
    run(go())


def test_subscribers_suite_lists_creators_and_management_actions(env):
    cfg, *_ = env
    subs.save([{"title": "My Creator", "url": "https://www.youtube.com/@creator/videos",
                "last_sync": "2026-10-17"}])
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(150, 42)) as pilot:
            app.query_one(TabbedContent).active = "tab-subs"
            await pilot.pause()
            table = app.query_one("#subs", DataTable)
            assert table.row_count == 1
            assert "My Creator" in str(table.get_row_at(0)[0])
            for selector in ("#sub-add", "#sub-remove", "#sub-explore", "#sub-check", "#sub-check-all"):
                app.query_one(selector)
    run(go())


def test_subscribers_add_button_opens_channel_entry(env):
    cfg, *_ = env
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(150, 42)) as pilot:
            app.query_one(TabbedContent).active = "tab-subs"
            await pilot.pause()
            await pilot.click("#sub-add")
            await pilot.pause()
            assert isinstance(app.screen, tui.ChannelAddScreen)
            assert app.screen.query_one("#channel-input", Input)
    run(go())


def test_enter_on_saved_creator_opens_recent_uploads(env, monkeypatch):
    cfg, *_ = env
    subs.save([{"title": "My Creator", "url": "https://www.youtube.com/@creator/videos",
                "last_sync": ""}])
    entries = [{"id": "clip1", "title": "Latest clip", "duration": 120,
                "url": "https://youtu.be/clip1"}]
    monkeypatch.setattr(subs, "fetch_recent", lambda url, cfg, n=15: ({"title": "My Creator", "entries": entries}, None))
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(150, 42)) as pilot:
            app.query_one(TabbedContent).active = "tab-subs"
            table = app.query_one("#subs", DataTable)
            table.focus()
            table.move_cursor(row=0)
            await pilot.press("enter")
            assert await wait_for(lambda: isinstance(app.screen, tui.ChannelExploreScreen))
            assert app.screen._entries[0]["title"] == "Latest clip"
    run(go())


def test_remove_selected_creator_requires_confirmation(env):
    cfg, *_ = env
    subs.save([{"title": "Remove Me", "url": "https://www.youtube.com/@remove/videos",
                "last_sync": ""}])
    async def go():
        app = tui.YtGrabApp(cfg)
        async with app.run_test(size=(150, 42)) as pilot:
            app.query_one(TabbedContent).active = "tab-subs"
            await pilot.pause()
            table = app.query_one("#subs", DataTable)
            table.move_cursor(row=0)
            await pilot.click("#sub-remove")
            await pilot.pause()
            assert isinstance(app.screen, tui.ChoiceScreen)
            await pilot.click("#c-remove")
            await pilot.pause()
            assert subs.load() == []
    run(go())
