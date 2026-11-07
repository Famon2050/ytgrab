"""Tests for 1.5.0: paginated sync, failure log + retry, per-subscription settings, sync --dry-run."""
import os
import subprocess
import sys

import pytest

from ytgrab import config, failed, flows, library, multi, pending, settings, subs

CFG = {"cookies_source": "none", "download_dir": "/tmp/x", "last_kind": "video",
       "last_video_quality": "720", "notify": False, "sync_depth": 15}


@pytest.fixture
def cdir(tmp_path, monkeypatch):
    d = tmp_path / "cfg"; d.mkdir()
    for mod in (config, library, pending, subs, settings):
        monkeypatch.setattr(mod, "CONFIG_DIR", d, raising=False)
    monkeypatch.setattr(config, "CONFIG_FILE", d / "config.json")
    monkeypatch.setattr(config, "HISTORY_FILE", d / "history.json")
    monkeypatch.setattr(library, "ARCHIVE_FILE", d / "archive.txt")
    monkeypatch.setattr(subs, "SUBS_FILE", d / "subs.json")
    return d


def vid(i, title=None, minutes=10):
    return {"id": f"v{i}", "title": title or f"video {i}", "duration": minutes * 60,
            "url": f"https://www.youtube.com/watch?v=v{i}"}


def channel(total, monkeypatch, calls=None):
    """A channel with `total` videos, newest first; fetch_recent honours `n` like yt-dlp's playlistend."""
    allv = [vid(i) for i in range(total)]
    def fake(url, cfg, n=15):
        if calls is not None: calls.append(n)
        return {"entries": allv[:n]}, None
    monkeypatch.setattr(subs, "fetch_recent", fake)
    return allv


# ---- 1. paginated sync --------------------------------------------------------------------
def test_sync_pages_back_until_it_reaches_a_known_video(cdir, monkeypatch):
    calls = []
    channel(100, monkeypatch, calls)
    library.archive_add([f"youtube v{i}" for i in range(40, 100)])            # have 40+ already
    new, err = subs.find_new(CFG, {"url": "c", "last_sync": "2026-10-01 10:00"})
    assert err == "" and [e["id"] for e in new] == [f"v{i}" for i in range(40)]   # all 40, not just 15
    assert calls == [15, 30, 60]


def test_first_sync_stays_shallow(cdir, monkeypatch):
    calls = []
    channel(100, monkeypatch, calls)
    new, _ = subs.find_new(CFG, {"url": "c", "last_sync": ""})
    assert len(new) == 15 and calls == [15]                    # never bulk-downloads a back catalogue


def test_paging_respects_the_cap_and_the_end_of_the_channel(cdir, monkeypatch):
    calls = []
    channel(1000, monkeypatch, calls)
    new, _ = subs.find_new(dict(CFG, sync_max_depth=40), {"url": "c", "last_sync": "x"})
    assert calls == [15, 30, 40] and len(new) == 40
    calls.clear(); channel(20, monkeypatch, calls)
    new, _ = subs.find_new(CFG, {"url": "c", "last_sync": "x"})
    assert calls == [15, 30] and len(new) == 20                # stops once the channel has no more


def test_lookup_errors_still_propagate(cdir, monkeypatch):
    monkeypatch.setattr(subs, "fetch_recent", lambda url, cfg, n=15: (None, "HTTP 404"))
    assert subs.find_new(CFG, {"url": "c", "last_sync": "x"}) == ([], "HTTP 404")


# ---- 2. per-subscription settings ---------------------------------------------------------
def test_parse_settings_valid():
    u, errs = subs.parse_sub_settings(["audio=MP3", "min=10", "max=90", "include=a|b", "exclude=live"])
    assert errs == [] and u == {"kind": "audio", "quality": "mp3", "min_minutes": 10.0,
                                "max_minutes": 90.0, "include": "a|b", "exclude": "live"}
    assert subs.parse_sub_settings(["video=720"])[0] == {"kind": "video", "quality": "720"}
    assert subs.parse_sub_settings(["format=default", "min=off"])[0] == {
        "kind": None, "quality": None, "min_minutes": None}
    assert set(subs.parse_sub_settings(["reset"])[0]) == set(subs._FIELDS)


@pytest.mark.parametrize("tok", ["audio=flac", "video=999", "min=abc", "min=-5", "include=(", "colour=red", "oops"])
def test_parse_settings_rejects_garbage(tok):
    assert subs.parse_sub_settings([tok])[1]


def test_max_below_min_is_rejected():
    assert any("smaller" in e for e in subs.parse_sub_settings(["min=30", "max=10"])[1])


def test_filters():
    es = [vid(1, "Great Tutorial", 12), vid(2, "LIVE: stream", 120), vid(3, "short clip", 1),
          vid(4, "Upcoming premiere", 0), vid(5, "Tutorial part 2", 45)]
    sub = {"include": "tutorial|premiere", "exclude": "live", "min_minutes": 5, "max_minutes": 60}
    keep, skipped = subs.apply_filters(es, sub)
    assert [e["id"] for e in keep] == ["v1", "v4", "v5"]        # unknown length (premiere) is kept
    assert [e["id"] for e in skipped] == ["v2", "v3"]
    assert subs.apply_filters(es, {}) == (es, [])
    assert subs.apply_filters(es, {"exclude": "("})[0] == es    # a hand-edited bad regex must not drop videos
    assert subs.apply_filters(es, {"include": "("})[0] == es


def test_set_command_persists_and_list_shows_it(cdir, capsys):
    subs.save([{"url": "u1", "title": "Alpha"}, {"url": "u2", "title": "Beta"}])
    assert subs.cmd_sub_set("beta", ["audio=m4a", "min=10", "exclude=live"])
    assert subs.load()[1]["kind"] == "audio" and subs.load()[1]["exclude"] == "live" and "kind" not in subs.load()[0]
    capsys.readouterr(); subs.cmd_sub_list()
    assert "audio m4a" in capsys.readouterr().out
    assert subs.cmd_sub_set("2", ["reset"]) and subs.describe_sub(subs.load()[1]) == ""
    assert not subs.cmd_sub_set("nope", ["min=1"]) and not subs.cmd_sub_set("1", ["bogus=1"])
    assert not subs.cmd_sub_set("1", [])


def test_override_for_ignores_invalid_formats():
    assert subs.override_for({"kind": "audio", "quality": "mp3"}) == ("audio", "mp3")
    assert subs.override_for({"kind": "audio", "quality": "720"}) == (None, None)
    assert subs.override_for({}) == (None, None)


def test_sync_applies_filters_and_formats(cdir, monkeypatch, tmp_path):
    subs.save([{"url": "chan", "title": "Pods", "last_sync": "", "kind": "audio", "quality": "mp3",
                "min_minutes": 5, "exclude": "trailer"}])
    es = [vid(1, "Episode 1", 30), vid(2, "Trailer", 30), vid(3, "tiny", 1)]
    monkeypatch.setattr(subs, "fetch_recent", lambda url, cfg, n=15: ({"entries": es}, None))
    got = {}
    monkeypatch.setattr(flows, "batch_download", lambda cfg, urls, **kw: got.update(urls=urls, **kw) or (len(urls), 0))
    assert subs.sync(dict(CFG, download_dir=str(tmp_path))) == 0
    assert got["urls"] == [es[0]["url"]]
    assert got["overrides"] == [{"kind": "audio", "quality": "mp3"}]
    assert {"youtube v2", "youtube v3"} <= library.archive_set()     # filtered ones are remembered


def test_per_subscription_format_reaches_the_job(monkeypatch, tmp_path):
    info = {"id": "a", "title": "A", "uploader": "U"}
    jobs = flows._build_jobs([info, dict(info, id="b")], ["u1", "u2"], "video", "720", {},
                             dict(CFG, download_dir=str(tmp_path)),
                             overrides=[{"kind": "audio", "quality": "mp3"}, None])
    assert (jobs[0].kind, jobs[0].quality) == ("audio", "mp3")
    assert "FFmpegExtractAudio" in [p["key"] for p in jobs[0].opts["postprocessors"]]
    assert (jobs[1].kind, jobs[1].quality) == ("video", "720")


# ---- 3. dry run ---------------------------------------------------------------------------
def test_dry_run_changes_nothing(cdir, monkeypatch, capsys):
    subs.save([{"url": "chan", "title": "Pods", "last_sync": "", "exclude": "trailer"}])
    monkeypatch.setattr(subs, "fetch_recent",
                        lambda url, cfg, n=15: ({"entries": [vid(1, "Episode 1"), vid(2, "Trailer")]}, None))
    monkeypatch.setattr(flows, "batch_download", lambda *a, **k: pytest.fail("dry run must not download"))
    before = (cdir / "subs.json").read_text()
    assert subs.sync(dict(CFG), dry_run=True) == 0
    out = capsys.readouterr().out
    assert "Episode 1" in out and "1 filtered out" in out and "Dry run: 1 video(s)" in out
    assert (cdir / "subs.json").read_text() == before                  # no last_sync stamp
    assert library.archive_set() == set()                              # filtered video NOT marked seen
    assert not (cdir / "sync.lock").exists() or config.try_exclusive("sync") is not None


def test_dry_run_runs_even_while_a_real_sync_holds_the_lock(cdir, monkeypatch):
    subs.save([{"url": "chan", "title": "P"}])
    monkeypatch.setattr(subs, "fetch_recent", lambda url, cfg, n=15: ({"entries": []}, None))
    held = config.try_exclusive("sync")
    try:
        assert subs.sync(dict(CFG), dry_run=True) == 0
    finally:
        config.release_exclusive(held)


# ---- 4. failure log + retry -----------------------------------------------------------------
def test_failure_log_basics(cdir):
    failed.record("u1", "T1", "boom", "video", "720", key="youtube a")
    failed.record("u1", "", "boom again")
    failed.record("u2", "T2", "nope")
    items = failed.load()
    assert [i["url"] for i in items] == ["u1", "u2"] and items[0]["attempts"] == 2
    assert items[0]["title"] == "T1" and items[0]["error"] == "boom again"
    assert failed.remove(["u1", "zzz"]) == 1 and failed.count() == 1
    assert failed.clear() == 1 and failed.count() == 0


def test_failure_log_is_capped(cdir):
    for i in range(failed.MAX_ENTRIES + 25):
        failed.record(f"u{i}")
    items = failed.load()
    assert len(items) == failed.MAX_ENTRIES and items[-1]["url"] == f"u{failed.MAX_ENTRIES + 24}"


def test_failed_jobs_are_logged_and_success_clears_them(cdir, monkeypatch):
    monkeypatch.setattr(flows, "_record_done", lambda cfg, j, quiet=False: failed.remove([j.url]) and True)
    bad = multi.Job(idx=1, url="https://youtu.be/bad", title="Bad", kind="audio", quality="mp3", opts={},
                    status="error", error="HTTP 403", archive_key="youtube bad")
    good = multi.Job(idx=2, url="https://youtu.be/good", title="Good", kind="video", quality="720", opts={}, status="done")
    failed.record("https://youtu.be/good", "Good", "earlier failure")

    class Fake:
        def __init__(self, jobs, workers=3, on_finish=None): self.cb = on_finish
        def run(self):
            self.cb(bad); self.cb(good)
            return [good], [bad]
    monkeypatch.setattr(flows, "MultiDownloader", Fake)
    assert flows._run_batch_jobs(dict(CFG), [bad, good], interactive=False) == (1, 1)
    items = failed.load()
    assert [i["url"] for i in items] == ["https://youtu.be/bad"]            # 'good' was forgiven
    assert items[0]["error"] == "HTTP 403" and items[0]["kind"] == "audio" and items[0]["key"] == "youtube bad"


def test_unreadable_urls_are_logged(cdir, monkeypatch):
    monkeypatch.setattr(flows, "fetch_info_quiet", lambda u, cfg: (None, "Private video"))
    flows.batch_download(dict(CFG), ["https://youtu.be/p"], interactive=False)
    assert failed.load()[0]["error"] == "Private video"


def test_retry_list_and_clear(cdir, capsys):
    assert flows.retry_command(dict(CFG)) == 0 and "No failed downloads" in capsys.readouterr().out
    failed.record("https://youtu.be/a", "Alpha", "HTTP 403")
    assert flows.retry_command(dict(CFG), list_only=True) == 0
    out = capsys.readouterr().out
    assert "Alpha" in out and "HTTP 403" in out and "1 failed" in out
    assert flows.retry_command(dict(CFG), clear=True) == 0 and failed.count() == 0


def test_retry_runs_only_what_is_still_missing(cdir, monkeypatch):
    failed.record("https://youtu.be/a", "A", "x", key="youtube a")
    failed.record("https://youtu.be/b", "B", "x", key="youtube b")
    library.archive_add(["youtube a"])                                      # got it another way meanwhile
    got = {}
    monkeypatch.setattr(flows, "batch_download", lambda cfg, urls, **kw: got.update(urls=urls, **kw) or (0, 1))
    assert flows.retry_command(dict(CFG)) == 1                              # still failing -> non-zero
    assert got["urls"] == ["https://youtu.be/b"] and got["interactive"] is False
    monkeypatch.setattr(flows, "batch_download", lambda cfg, urls, **kw: (len(urls), 0))
    assert flows.retry_command(dict(CFG)) == 0


def test_cli_wiring(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    run = lambda *a: subprocess.run([sys.executable, "-m", "ytgrab.main", *a], capture_output=True, text=True, env=env)
    r = run("retry", "--list")
    assert r.returncode == 0 and "No failed downloads" in r.stdout
    r = run("sync", "--dry-run")
    assert r.returncode == 0 and "No subscriptions" in r.stdout
    r = run("sub", "set", "1", "audio=mp3")
    assert r.returncode == 1 and "No such subscription" in r.stdout
    r = run("sub", "set", "1", "audio=flac")
    assert r.returncode == 1 and "pick one of" in r.stdout and "Traceback" not in r.stderr
    assert "failed downloads" in run("doctor").stdout
