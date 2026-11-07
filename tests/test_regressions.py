"""Regression tests for the 1.4.1 bug-fix release. One test per bug that was found."""
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yt_dlp

from ytgrab import config, flows, library, multi, pending, selfupdate as su, settings, subs, ytdl
from ytgrab.fmt import parse_clip_range, parse_rate, short_error

CFG = {"cookies_source": "none", "download_dir": "/tmp/x", "last_kind": "video",
       "last_video_quality": "best", "notify": False}
STRIP = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def cdir(tmp_path, monkeypatch):
    d = tmp_path / "cfg"; d.mkdir()
    for mod in (config, library, pending, subs, settings):
        monkeypatch.setattr(mod, "CONFIG_DIR", d, raising=False)
    monkeypatch.setattr(config, "CONFIG_FILE", d / "config.json")
    monkeypatch.setattr(config, "HISTORY_FILE", d / "history.json")
    monkeypatch.setattr(config, "COOKIES_FILE", d / "cookies.txt")
    monkeypatch.setattr(settings, "COOKIES_FILE", d / "cookies.txt")
    monkeypatch.setattr(library, "ARCHIVE_FILE", d / "archive.txt")
    monkeypatch.setattr(pending, "QUEUE_FILE", d / "queue.json")
    monkeypatch.setattr(subs, "SUBS_FILE", d / "subs.json")
    return d


# ---- 1. self-update must never turn a relative folder into a PyPI name ------------------
def test_install_command_is_always_an_absolute_path(tmp_path, monkeypatch):
    proj = tmp_path / "ytgrab"; proj.mkdir()
    monkeypatch.chdir(tmp_path)
    for use_pipx in (False, True):
        monkeypatch.setattr(su.shutil, "which", lambda n: "/usr/bin/pipx")
        spec = su.install_command(Path("ytgrab"), use_pipx=use_pipx)[-1]
        assert spec == f"{proj.resolve()}[tui]" and os.path.isabs(spec[:-5])


def test_version_ordering_handles_prereleases():
    v = su.version_tuple
    assert v("1.4.1") > v("1.4.0") > v("1.4.0rc1") > v("1.3.9")
    assert v("1.10.0") > v("1.9.9") and v("v2.0") > v("1.99.99")


def test_git_pull_never_prompts(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    seen = {}
    def fake_run(cmd, **kw):
        seen["env"] = kw["env"]; seen["timeout"] = kw["timeout"]
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(su.shutil, "which", lambda n: "/usr/bin/git")
    monkeypatch.setattr(su.subprocess, "run", fake_run)
    ok, msg = su.git_pull(tmp_path)
    assert ok is False and "timed out" in msg
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0" and seen["timeout"] > 0


# ---- 2. cookies are secrets ---------------------------------------------------------------
def test_cookie_import_is_owner_only(cdir, tmp_path):
    src = tmp_path / "c.txt"; src.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\n" * 5)
    settings.cmd_cookies_import(str(src))
    assert (cdir / "cookies.txt").stat().st_mode & 0o777 == 0o600


def test_harden_permissions_fixes_old_installs(cdir):
    (cdir / "cookies.txt").write_text("x"); os.chmod(cdir / "cookies.txt", 0o644); os.chmod(cdir, 0o755)
    config.harden_permissions()
    assert (cdir / "cookies.txt").stat().st_mode & 0o777 == 0o600
    assert cdir.stat().st_mode & 0o777 == 0o700


def test_cookies_clear_also_wipes_backup_copies(cdir):
    (cdir / "cookies.txt").write_text("secret")
    bk = cdir.parent / (cdir.name + ".bak-20260101-000000"); bk.mkdir()
    (bk / "cookies.txt").write_text("secret"); (bk / "config.json").write_text("{}")
    settings.cmd_cookies_clear()
    assert not (cdir / "cookies.txt").exists() and not (bk / "cookies.txt").exists()
    assert (bk / "config.json").exists()                 # only the secret goes


# ---- 3. the pending queue must keep what did not download ---------------------------------
def test_queue_keeps_failed_items(cdir, monkeypatch, capsys):
    for u in ("https://youtu.be/a", "https://youtu.be/b", "https://youtu.be/c"):
        pending.add(u, u)
    answers = iter(["all", "", "", ""])
    monkeypatch.setattr(flows, "prompt", lambda *a, **k: next(answers))
    def fake_batch(cfg, urls, done_urls=None, **k):
        done_urls.add("https://youtu.be/a")              # only #1 succeeds
        return 1, 2
    monkeypatch.setattr(flows, "batch_download", fake_batch)
    flows.process_queue(dict(config.DEFAULTS))
    assert [i["url"] for i in pending.all_items()] == ["https://youtu.be/b", "https://youtu.be/c"]
    assert "stay in the queue" in capsys.readouterr().out


# ---- 4. unreadable videos are failures, not silence -------------------------------------
def test_unreadable_urls_count_as_failed(monkeypatch, capsys):
    monkeypatch.setattr(flows, "fetch_info_quiet", lambda u, cfg: (None, "Video unavailable"))
    done, failed = flows.batch_download(dict(CFG), ["https://youtu.be/aaaaaaaaaaa", "https://youtu.be/bbbbbbbbbbb"],
                                        interactive=False)
    out = capsys.readouterr().out
    assert (done, failed) == (0, 2)
    assert "could not read https://youtu.be/aaaaaaaaaaa" in out and "Video unavailable" in out


def test_get_exit_status_reflects_unreadable_playlist_entries(monkeypatch):
    info = {"_type": "playlist", "title": "P", "entries": [{"id": "a", "title": "A", "url": "https://youtu.be/a"}]}
    # the playlist itself reads fine, but its only video is private
    monkeypatch.setattr(flows, "fetch_info_quiet",
                        lambda u, cfg: (info, None) if "playlist" in u else (None, "Private video"))
    assert flows.get_command(dict(CFG), ["https://www.youtube.com/playlist?list=PLx"]) == 1


# ---- 5. every finished download is recorded immediately ------------------------------------
def test_jobs_are_recorded_as_they_finish_not_at_the_end(monkeypatch):
    recorded = []
    monkeypatch.setattr(flows, "_record_done", lambda cfg, j, quiet=False: recorded.append(j.url) or True)
    j1 = multi.Job(idx=1, url="u1", title="one", kind="video", quality="best", opts={}, status="done")
    j2 = multi.Job(idx=2, url="u2", title="two", kind="video", quality="best", opts={})

    class Crashy:                                    # job 1 finishes, then the whole process dies
        def __init__(self, jobs, workers=3, on_finish=None): self.cb = on_finish
        def run(self):
            self.cb(j1)
            raise RuntimeError("killed")
    monkeypatch.setattr(flows, "MultiDownloader", Crashy)
    with pytest.raises(RuntimeError):
        flows._run_batch_jobs(dict(CFG), [j1, j2], interactive=False)
    assert recorded == ["u1"]                        # survived the crash


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL inside the download engine."""
    def __init__(self, opts): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def extract_info(self, url, download=True):
        if url == "boom":
            raise Exception()                        # empty message: used to crash the worker
        if url == "err":
            raise RuntimeError("ERROR: [youtube] abc: Video unavailable\nsecond line")
        return {"requested_downloads": [{"filepath": ""}]}


def test_multidownloader_calls_on_finish_per_job(monkeypatch):
    monkeypatch.setattr(multi.yt_dlp, "YoutubeDL", FakeYDL)
    seen = []
    jobs = [multi.Job(idx=i, url=u, title=u, kind="video", quality="best", opts={})
            for i, u in enumerate(("ok", "err"), 1)]
    done, errs = multi.MultiDownloader(jobs, workers=2, on_finish=lambda j: seen.append((j.url, j.status))).run()
    assert sorted(seen) == [("err", "error"), ("ok", "done")] and len(done) == 1 and len(errs) == 1


# ---- 6. config / history survive corruption, crashes and concurrency ------------------
def test_corrupt_history_is_quarantined_not_overwritten(cdir):
    (cdir / "history.json").write_text('[{"title": "precious"')            # truncated JSON
    assert config.load_history() == []
    config.add_history({"title": "new"})
    saved = list(cdir.glob("history.json.corrupt-*"))
    assert saved and "precious" in saved[0].read_text()                     # old bytes kept
    assert [e["title"] for e in config.load_history()] == ["new"]


def test_corrupt_config_falls_back_but_keeps_the_file(cdir):
    (cdir / "config.json").write_text("{nope")
    cfg = config.load_config()
    assert cfg["theme"] == config.DEFAULTS["theme"] and list(cdir.glob("config.json.corrupt-*"))


def test_writes_are_atomic_and_leave_no_temp_files(cdir):
    config.save_config({"a": 1})
    assert json.loads((cdir / "config.json").read_text()) == {"a": 1}
    assert not list(cdir.glob("*.tmp"))


def test_concurrent_history_writes_lose_nothing(cdir):
    ts = [threading.Thread(target=config.add_history, args=({"title": str(i)},)) for i in range(40)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(config.load_history()) == 40


def test_concurrent_pending_adds_lose_nothing(cdir):
    ts = [threading.Thread(target=pending.add, args=(f"https://youtu.be/{i}",)) for i in range(30)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert pending.count() == 30


# ---- 7. the live table must never wrap or overflow ------------------------------------------
@pytest.mark.parametrize("cols", [60, 80, 120])
def test_table_rows_fit_the_terminal(monkeypatch, cols):
    monkeypatch.setattr(multi.shutil, "get_terminal_size", lambda fb=(80, 24): os.terminal_size((cols, 24)))
    j = multi.Job(idx=1, url="u", title="日本語のとても長いタイトル" * 6, kind="video", quality="1080", opts={})
    m = multi.MultiDownloader([j])
    for st in ("queued", "downloading", "paused", "processing", "done", "error", "cancelled"):
        j.status, j.speed, j.pct = st, "12.3 MB/s", 40
        row = STRIP.sub("", m._row(j))
        assert sum(2 if ord(ch) > 0x2E80 else 1 for ch in row) < cols, (cols, st)


def test_long_batches_scroll_instead_of_overflowing(monkeypatch):
    monkeypatch.setattr(multi.shutil, "get_terminal_size", lambda fb=(80, 24): os.terminal_size((80, 24)))
    jobs = [multi.Job(idx=i, url=str(i), title=f"v{i}", kind="video", quality="best", opts={}) for i in range(1, 101)]
    for j in jobs[:60]:
        j.status = "done"
    m = multi.MultiDownloader(jobs)
    shown, foot = m._window()
    assert len(shown) + 1 <= 24 - 8 and shown[0].idx == 61 and "60 done" in STRIP.sub("", foot)


def test_no_cursor_codes_when_output_is_piped(monkeypatch, capsys):
    j = multi.Job(idx=1, url="u", title="t", kind="video", quality="best", opts={}, status="done")
    m = multi.MultiDownloader([j]); m._tty = False
    m._render(); m._render()
    assert capsys.readouterr().out == ""
    m._finished(j)
    assert "✓ t" in capsys.readouterr().out         # a plain log line instead


# ---- 8. error handling ----------------------------------------------------------------
def test_empty_exception_message_no_longer_crashes_the_worker(monkeypatch):
    monkeypatch.setattr(multi.yt_dlp, "YoutubeDL", FakeYDL)
    j = multi.Job(idx=1, url="boom", title="t", kind="video", quality="best", opts={})
    multi.JobRunner(quiet=True)._run_one(j)
    assert j.status == "error" and j.error == "Exception"


def test_short_error_is_readable():
    assert short_error(RuntimeError("\x1b[0;31mERROR:\x1b[0m [youtube] x: gone")) == "[youtube] x: gone"
    assert short_error(Exception()) == "Exception"
    assert short_error(RuntimeError("ERROR: [youtube] abc: gone\nmore")) == "[youtube] abc: gone"
    assert len(short_error(RuntimeError("x" * 999))) == 200


# ---- 9. input validation ----------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [("2M", 2 * 1024 ** 2), ("500K", 512000), ("1.5MB/s", int(1.5 * 1024 ** 2)),
                                          ("2048", 2048), ("1.2.3M", None), ("100", None), ("abc", None), ("", None)])
def test_parse_rate(raw, expected):
    assert parse_rate(raw) == expected


@pytest.mark.parametrize("raw", ["1:30-", "-", "abc-def", "1:90-2:00"])
def test_clip_range_errors_are_clean(raw):
    with pytest.raises(ValueError):
        parse_clip_range(raw)
    assert parse_clip_range("1:30-2:00") == (90, 120)


def test_bad_speed_limit_flag_is_a_clean_error(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    r = subprocess.run([sys.executable, "-m", "ytgrab.main", "-l", "1.2.3M", "stats"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 2 and "Invalid rate" in r.stdout and "Traceback" not in r.stderr


def test_preset_flag_does_not_rewrite_saved_settings(monkeypatch):
    monkeypatch.setattr(ytdl, "save_config", lambda cfg: pytest.fail("must not persist"))
    cfg = dict(config.DEFAULTS)
    assert ytdl.apply_preset(cfg, "music", persist=False) and cfg["last_kind"] == "audio"


# ---- 10. logs stay clean when piped (cron) ----------------------------------------------
def _piped(code, **env_over):
    env = {k: v for k, v in os.environ.items() if "COLOR" not in k}
    env.update(env_over)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, env=env).stdout


def test_no_escape_codes_or_spinner_frames_when_piped():
    out = _piped("import time\nfrom ytgrab.ui import Spinner\nfrom ytgrab.theme import paint, C, clear_screen\n"
                 "sp = Spinner('x'); sp.start(); time.sleep(0.3); sp.stop(); clear_screen()\nprint(paint('hi', C.RED))")
    assert out == b"hi\n"


def test_colour_still_works_when_forced():
    assert b"\x1b[" in _piped("from ytgrab.theme import paint, C\nprint(paint('hi', C.RED))", YTGRAB_FORCE_COLOR="1")


# ---- 11. Shorts / format choice and filenames (checked against real yt-dlp) ----------------
def _fmt(fid, w, h, vc="avc1", ext="mp4", tbr=1000):
    return {"format_id": fid, "width": w, "height": h, "vcodec": vc, "acodec": "none", "fps": 30,
            "ext": ext, "tbr": tbr, "protocol": "https", "url": "http://x/" + fid}


_AUDIO = [{"format_id": "a-aac", "vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a", "abr": 128, "tbr": 128,
           "protocol": "https", "url": "http://x/a1"},
          {"format_id": "a-opus", "vcodec": "none", "acodec": "opus", "ext": "webm", "abr": 160, "tbr": 160,
           "protocol": "https", "url": "http://x/a2"}]


def _pick(formats, quality, **cfg):
    o = ytdl.build_video_opts(quality, "/tmp", dict(CFG, **cfg))
    y = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "format": o["format"],
                          "format_sort": o["format_sort"], "merge_output_format": "mp4"})
    info = {"id": "x", "title": "t", "extractor": "generic", "extractor_key": "Generic",
            "webpage_url": "http://x", "formats": [dict(f) for f in formats]}
    r = y.process_video_result(info, download=False)
    return "+".join(p["format_id"] for p in (r.get("requested_formats") or [r]))


def test_portrait_shorts_get_the_quality_you_asked_for():
    shorts = [_fmt("s360", 360, 640), _fmt("s720", 720, 1280), _fmt("s1080", 1080, 1920)] + _AUDIO
    assert _pick(shorts, "720").startswith("s720")           # the old height<=720 filter chose s360
    assert _pick(shorts, "1080").startswith("s1080")


def test_same_resolution_prefers_h264_aac_but_never_costs_resolution():
    land = [_fmt("l1080", 1920, 1080), _fmt("l1080vp9", 1920, 1080, vc="vp09", ext="webm", tbr=1500),
            _fmt("l2160vp9", 3840, 2160, vc="vp09", ext="webm", tbr=9000)] + _AUDIO
    assert _pick(land, "1080") == "l1080+a-aac"
    assert _pick(land, "best").startswith("l2160vp9")        # resolution still wins
    assert _pick(land, "1080", prefer_compat=False) == "l1080vp9+a-opus"


def test_long_unicode_titles_fit_filename_limits(tmp_path):
    name = yt_dlp.YoutubeDL({"quiet": True}).evaluate_outtmpl(
        ytdl.playlist_template(tmp_path, "P", 1), {"title": "日本語" * 100, "ext": "mp4"})
    assert len(os.path.basename(name).encode()) <= 255


# ---- 12. sync ----------------------------------------------------------------------------------
def _two_subs(cdir, monkeypatch):
    subs.save([{"url": "https://www.youtube.com/@a/videos", "title": "A"},
               {"url": "https://www.youtube.com/@b/videos", "title": "B"}])
    shared = {"id": "s1", "title": "collab", "url": "https://www.youtube.com/watch?v=s1"}
    monkeypatch.setattr(subs, "fetch_recent", lambda url, cfg, n=15: ({"entries": [shared]}, None))


def test_sync_downloads_a_shared_video_once(cdir, monkeypatch):
    _two_subs(cdir, monkeypatch)
    got = {}
    monkeypatch.setattr(flows, "batch_download", lambda cfg, urls, **k: got.update(urls=urls) or (len(urls), 0))
    assert subs.sync(dict(CFG)) == 0
    assert got["urls"] == ["https://www.youtube.com/watch?v=s1"]
    assert all(s["last_sync"] for s in subs.load())


def test_overlapping_sync_runs_do_not_collide(cdir, monkeypatch, capsys):
    _two_subs(cdir, monkeypatch)
    monkeypatch.setattr(flows, "batch_download", lambda *a, **k: pytest.fail("second run must back off"))
    held = config.try_exclusive("sync")                      # "another cron run" holds the lock
    try:
        assert subs.sync(dict(CFG)) == 0
    finally:
        config.release_exclusive(held)
    assert "already running" in capsys.readouterr().out
    assert config.try_exclusive("sync") is not None          # and it is released afterwards
