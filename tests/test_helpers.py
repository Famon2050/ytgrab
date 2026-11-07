"""Regression tests for the bugs fixed in 1.0.1. Run:  pytest"""
import subprocess

import pytest

from ytgrab import ytdl, pending
from ytgrab.extras import is_youtube_url, parse_selection
from ytgrab.fmt import fmt_duration, fmt_size, parse_clip_range, parse_rate


# ---- URL handling -------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc",
    "https://youtu.be/abc",
    "https://m.youtube.com/watch?v=abc",
    "https://music.youtube.com/watch?v=abc",
    "https://www.youtube.com/shorts/abc",
    "https://www.youtube.com/live/abc",
    "https://www.youtube.com/playlist?list=PL1",
])
def test_is_youtube_url_accepts(url):
    assert is_youtube_url(url)


@pytest.mark.parametrize("url", [
    "", "hello world", "https://example.com/watch?v=abc",
    "https://evil.com/?u=youtube.com/watch", "https://youtu.be/",
    "https://notyoutube.com/watch?v=1", None,
])
def test_is_youtube_url_rejects(url):
    assert not is_youtube_url(url)


def test_strip_playlist_param_keeps_encoding():
    out = ytdl.strip_playlist_param(
        "https://www.youtube.com/watch?v=abc&list=PL1&index=3&t=1m30s&si=a%20b")
    assert "list=" not in out and "index=" not in out
    assert "v=abc" in out and "t=1m30s" in out
    assert "a b" not in out            # must stay percent-encoded


def test_has_playlist_param():
    assert ytdl.has_playlist_param("https://youtube.com/watch?v=a&list=PL")
    assert not ytdl.has_playlist_param("https://youtube.com/watch?v=a")


# ---- formatting / parsing ----------------------------------------------
def test_clip_range():
    assert parse_clip_range("1:00-2:30") == (60, 150)
    assert parse_clip_range("whole") is None
    with pytest.raises(ValueError):
        parse_clip_range("2:00-1:00")
    with pytest.raises(ValueError):
        parse_clip_range("abc")


def test_selection():
    assert parse_selection("1,3,5-7", 10) == [1, 3, 5, 6, 7]
    assert parse_selection("all", 3) == [1, 2, 3]
    assert parse_selection("99", 3) == []


def test_fmt():
    assert fmt_duration(3661) == "1:01:01"
    assert fmt_size(1536) == "1.5 KB"
    assert parse_rate("2M") == 2 * 1024 ** 2


# ---- security: titles must never reach a shell --------------------------
def test_notify_never_uses_shell(monkeypatch):
    calls = []
    monkeypatch.setattr(ytdl.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(ytdl.sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, **kw: calls.append((cmd, kw)))
    evil = 'x"; touch /tmp/PWNED; "$(touch /tmp/PWNED)'
    ytdl.notify("ytgrab", evil)
    cmd, kw = calls[0]
    assert isinstance(cmd, list) and evil in cmd      # passed as one argv item
    assert not kw.get("shell")


def test_open_path_never_uses_shell(monkeypatch):
    calls = []
    monkeypatch.setattr(ytdl.sys, "platform", "linux")
    monkeypatch.setattr(ytdl.os, "name", "posix")
    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, **kw: calls.append((cmd, kw)))
    ytdl.open_path('/tmp/$(id) "x".mp4')
    assert calls[0][0] == ["xdg-open", '/tmp/$(id) "x".mp4']


# ---- yt-dlp options ------------------------------------------------------
CFG = {"sponsorblock": True, "cookies_source": "none"}


def _keys(opts):
    return [p["key"] for p in opts.get("postprocessors", [])]


def test_sponsorblock_uses_postprocessors(tmp_path):
    for opts in (ytdl.build_video_opts("720", tmp_path, CFG),
                 ytdl.build_audio_opts("mp3", tmp_path, CFG)):
        keys = _keys(opts)
        assert "SponsorBlock" in keys and "ModifyChapters" in keys
        assert keys.index("SponsorBlock") < keys.index("ModifyChapters")
        assert "sponsorblock_remove" not in opts      # the dead CLI-only key


def test_sponsorblock_off_by_default(tmp_path):
    opts = ytdl.build_video_opts("720", tmp_path, {"cookies_source": "none"})
    assert "SponsorBlock" not in _keys(opts)


def test_metadata_fetch_does_not_run_sponsorblock(monkeypatch):
    seen = {}
    monkeypatch.setattr(ytdl, "_extract_with_auth",
                        lambda url, opts, download: (seen.update(opts) or {}, None))
    monkeypatch.setattr(ytdl, "Spinner",
                        lambda *a, **k: type("S", (), {"start": lambda s: None,
                                                       "stop": lambda s: None})())
    ytdl.fetch_info("https://youtu.be/x", CFG)
    assert "postprocessors" not in seen
    assert seen["extract_flat"] == "in_playlist"      # fast playlists


def test_audio_thumbnail_only_when_embedded(tmp_path):
    cfg = {"cookies_source": "none"}
    assert ytdl.build_audio_opts("mp3", tmp_path, cfg).get("writethumbnail")
    assert not ytdl.build_audio_opts("opus", tmp_path, cfg).get("writethumbnail")
    assert not ytdl.build_audio_opts("wav", tmp_path, cfg).get("writethumbnail")


# ---- pending queue -------------------------------------------------------
def test_pending_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(pending, "QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(pending, "CONFIG_DIR", tmp_path)
    assert pending.add("https://youtu.be/a", "A")
    assert not pending.add("https://youtu.be/a", "A")   # dedupe
    assert pending.count() == 1
    assert pending.remove_by_url("https://youtu.be/a")
    assert pending.count() == 0
