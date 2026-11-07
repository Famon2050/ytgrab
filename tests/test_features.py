"""Tests for the 1.1.0 features."""
import os
import subprocess
import sys

import pytest

from ytgrab import config, library, subs, ytdl
from ytgrab.fmt import fmt_views_short, parse_length_filter
from ytgrab.flows import _build_jobs
from ytgrab.multi import Job, MultiDownloader
from ytgrab.search import filter_results


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(library, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(library, "ARCHIVE_FILE", tmp_path / "archive.txt")
    monkeypatch.setattr(subs, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(subs, "SUBS_FILE", tmp_path / "subs.json")
    monkeypatch.setattr(ytdl, "save_config", lambda cfg: None)
    return tmp_path


# ---- archive -------------------------------------------------------------
def test_archive_roundtrip(cfgdir):
    assert library.archive_set() == set()
    library.archive_add(["youtube a", "youtube b", None, "youtube a"])
    library.archive_add(["youtube b", "youtube c"])
    assert library.archive_set() == {"youtube a", "youtube b", "youtube c"}
    assert library.archive_clear()
    assert library.archive_set() == set()


def test_archive_key():
    assert library.archive_key({"id": "x1", "extractor_key": "Youtube"}) == "youtube x1"
    assert library.archive_key({"id": "x1"}) == "youtube x1"
    assert library.archive_key({}) is None
    assert library.key_for_entry({"id": "z"}) == "youtube z"


def test_build_jobs_skips_archived_and_applies_templates(cfgdir, tmp_path):
    cfg = dict(config.DEFAULTS, download_dir=str(tmp_path), cookies_source="none")
    library.archive_add(["youtube have"])
    infos = [{"id": "have", "title": "A", "extractor_key": "Youtube"},
             {"id": "new", "title": "B", "extractor_key": "Youtube", "uploader": "Chan"}]
    urls = ["u1", "u2"]
    tmpls = ["T1/%(title)s.%(ext)s", "T2/%(title)s.%(ext)s"]
    jobs = _build_jobs(infos, urls, "video", "720", {}, cfg, outtmpls=tmpls, use_archive=True)
    assert [j.url for j in jobs] == ["u2"]
    assert jobs[0].opts["outtmpl"] == "T2/%(title)s%(height& {}p)s.%(ext)s"   # quality stamp stays aligned
    assert jobs[0].channel == "Chan" and jobs[0].archive_key == "youtube new"
    # without the archive nothing is skipped
    assert len(_build_jobs(infos, urls, "video", "720", {}, cfg)) == 2
    # and the setting can switch it off
    cfg["use_archive"] = False
    assert len(_build_jobs(infos, urls, "video", "720", {}, cfg, use_archive=True)) == 2


# ---- hooks (no shell) ------------------------------------------------------
def test_hook_argv():
    assert library.hook_argv("mv {file} /dst", "/a b/c.mp4") == ["mv", "/a b/c.mp4", "/dst"]
    assert library.hook_argv("echo hi", "/x.mp4") == ["echo", "hi", "/x.mp4"]
    evil = "/tmp/$(touch PWNED); rm -rf ~.mp4"
    assert library.hook_argv("cmd {file}", evil) == ["cmd", evil]     # one argv item
    assert library.hook_argv("   ", "/x") == []


def test_run_post_hook_passes_env_and_path(tmp_path):
    out = tmp_path / "out.txt"
    script = ("import os,sys;open(sys.argv[1],'w').write("
              "os.environ['YTGRAB_TITLE']+'|'+sys.argv[2])")
    cfg = {"post_hook": f"{sys.executable} -c \"{script}\" {out} {{file}}"}
    f = tmp_path / "vid $(id).mp4"
    assert library.run_post_hook(cfg, f, title='T"; id #', url="u", kind="video")
    assert out.read_text() == f'T"; id #|{f}'
    assert library.run_post_hook({}, f) is None
    assert library.run_post_hook({"post_hook": "definitely-not-a-command-xyz"}, f) is False


# ---- templates --------------------------------------------------------------
def test_templates_escape_percent_and_slashes(tmp_path):
    t = ytdl.playlist_template(tmp_path / "100%", "Top 10 / best %", 7)
    assert "100%%" in t and "best %%" in t
    assert t.endswith("007 - %(title).150B.%(ext)s")
    assert "/best" not in t.replace(str(tmp_path), "")      # '/' in title can't nest
    assert ytdl.channel_template(tmp_path, "AC/DC").count(os.sep) == str(tmp_path).count(os.sep) + 2


# ---- presets ----------------------------------------------------------------
def test_profiles(cfgdir):
    cfg = config.load_config()
    cfg.update(last_kind="audio", last_audio_format="opus", sponsorblock=True)
    assert ytdl.save_profile(cfg, "Podcast")[0]
    assert not ytdl.save_profile(cfg, "music")[0]        # built-in name
    assert not ytdl.save_profile(cfg, "list")[0]         # reserved word
    assert not ytdl.save_profile(cfg, "bad name!")[0]
    cfg.update(last_kind="video", sponsorblock=False)
    assert ytdl.apply_preset(cfg, "podcast")
    assert cfg["last_kind"] == "audio" and cfg["sponsorblock"] is True
    assert ytdl.delete_profile(cfg, "podcast") and not ytdl.apply_preset(cfg, "podcast")
    assert ytdl.apply_preset(cfg, "music")               # built-ins still work


def test_defaults_not_shared(cfgdir):
    a = config.load_config(); a["profiles"]["x"] = {}
    assert config.load_config()["profiles"] == {} and config.DEFAULTS["profiles"] == {}


# ---- subscriptions ----------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("@veritasium", "https://www.youtube.com/@veritasium/videos"),
    ("https://www.youtube.com/@veritasium/", "https://www.youtube.com/@veritasium/videos"),
    ("https://www.youtube.com/channel/UC123", "https://www.youtube.com/channel/UC123/videos"),
    ("https://www.youtube.com/@x/videos", "https://www.youtube.com/@x/videos"),
    ("https://www.youtube.com/playlist?list=PL1", "https://www.youtube.com/playlist?list=PL1"),
])
def test_normalize_channel_url(raw, expected):
    assert subs.normalize_channel_url(raw) == expected


def test_sub_remove(cfgdir):
    subs.save([{"url": "u1", "title": "Alpha"}, {"url": "u2", "title": "Beta"}])
    assert subs.cmd_sub_remove("beta") and [s["title"] for s in subs.load()] == ["Alpha"]
    assert subs.cmd_sub_remove("1") and subs.load() == []
    assert not subs.cmd_sub_remove("nope")


def test_sync_only_downloads_unseen(cfgdir, tmp_path, monkeypatch):
    cfg = dict(config.DEFAULTS, download_dir=str(tmp_path))
    subs.save([{"url": "chan", "title": "My Chan", "last_sync": ""}])
    library.archive_add(["youtube old"])
    entries = [{"id": i, "title": i, "url": f"https://youtu.be/{i}"} for i in ("new1", "old", "new2")]
    monkeypatch.setattr(subs, "fetch_recent", lambda url, cfg, n=15: ({"entries": entries}, None))
    captured = {}
    import ytgrab.flows as flows
    monkeypatch.setattr(flows, "batch_download",
                        lambda cfg, urls, **kw: captured.update(urls=urls, **kw) or (len(urls), 0))
    assert subs.sync(cfg) == 0
    assert captured["urls"] == ["https://youtu.be/new1", "https://youtu.be/new2"]
    assert all("My Chan" in t for t in captured["outtmpls"]) and captured["interactive"] is False
    assert subs.load()[0]["last_sync"]                     # timestamp recorded


# ---- search / fmt -----------------------------------------------------------
def test_length_filter():
    assert parse_length_filter("10-60") == (600, 3600)
    assert parse_length_filter(">20") == (1200, None)
    assert parse_length_filter("<5") == (None, 300)
    assert parse_length_filter("") == (None, None)
    for bad in ("30", "60-10", "abc"):
        with pytest.raises(ValueError):
            parse_length_filter(bad)
    res = [{"duration": 120}, {"duration": 900}, {"duration": 0}]
    assert filter_results(res, 600, None) == [{"duration": 900}]
    assert filter_results(res) == res


def test_views_short():
    assert [fmt_views_short(n) for n in (0, 950, 1500, 2_000_000, 3_400_000_000)] == \
           ["?", "950", "1.5K", "2M", "3.4B"]


# ---- multi-downloader -------------------------------------------------------
def test_job_reset_and_renumber():
    jobs = [Job(idx=5, url="a", title="a", kind="video", quality="720", opts={}),
            Job(idx=9, url="b", title="b", kind="video", quality="720", opts={})]
    MultiDownloader(jobs)
    assert [j.idx for j in jobs] == [1, 2]                # selection keys match rows
    jobs[0].status = "error"; jobs[0].error = "boom"; jobs[0]._cancel.set()
    jobs[0].reset()
    assert jobs[0].status == "queued" and not jobs[0]._cancel.is_set() and jobs[0].error == ""


# ---- video options ------------------------------------------------------------
def test_video_embed_options(tmp_path):
    cfg = dict(config.DEFAULTS, cookies_source="none")
    keys = [p["key"] for p in ytdl.build_video_opts("720", tmp_path, cfg, subtitles=True)["postprocessors"]]
    assert keys == ["FFmpegMetadata", "FFmpegEmbedSubtitle"]  # MP4 default needs no extra remux
    keys = [p["key"] for p in ytdl.build_video_opts("720", tmp_path, cfg)["postprocessors"]]
    assert keys == ["FFmpegMetadata"]
    off = dict(cfg, embed_metadata=False, embed_subs=False)
    assert "postprocessors" not in ytdl.build_video_opts("720", tmp_path, off, subtitles=True)
    assert ytdl.build_video_opts("720", tmp_path, dict(cfg, retries=3))["retries"] == 3
    assert ytdl.build_video_opts("720", tmp_path, dict(cfg, retries="x"))["retries"] == 10


# ---- NO_COLOR ----------------------------------------------------------------
def test_no_color():
    code = "from ytgrab.theme import paint, C; print(repr(paint('hi', C.RED, C.BOLD)))"
    colored = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             env={k: v for k, v in os.environ.items() if k != "NO_COLOR"})
    plain = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=dict(os.environ, NO_COLOR="1"))
    assert "\\x1b" in colored.stdout and plain.stdout.strip() == "'hi'"
