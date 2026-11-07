import pytest

from ytgrab import config, ytdl
from ytgrab.multi import Job, MultiDownloader


@pytest.mark.parametrize(("container", "expected"), [("mp4", "mp4"), ("mkv", "mkv"), ("AVI", "mp4"), (None, "mp4")])
def test_video_container_is_limited_to_mp4_or_mkv(tmp_path, container, expected):
    cfg = {"video_container": container, "cookies_source": "none", "embed_metadata": True}
    opts = ytdl.build_video_opts("1080", tmp_path, cfg)
    assert opts["merge_output_format"] == expected
    assert opts["outtmpl"].endswith("%(title).150B%(height& {}p)s.%(ext)s")
    from yt_dlp import YoutubeDL
    with YoutubeDL({"outtmpl": opts["outtmpl"], "outtmpl_na_placeholder": "", "quiet": True}) as ydl:
        assert ydl.prepare_filename({"title": "Kamata Official Video", "height": 1080, "ext": expected}) == str(tmp_path / f"Kamata Official Video 1080p.{expected}")
    remux = [pp for pp in opts["postprocessors"] if pp["key"] == "FFmpegVideoRemuxer"]
    assert bool(remux) is (expected == "mkv")
    if remux:
        assert remux[0]["preferedformat"] == "mkv"


def test_quality_template_works_for_playlist_and_directory_templates(tmp_path):
    template = ytdl.playlist_template(tmp_path, "my list", 3)
    opts = ytdl.build_video_opts("720", tmp_path, {"cookies_source": "none"}, outtmpl=template)
    assert "003 - %(title).150B%(height& {}p)s.%(ext)s" in opts["outtmpl"]


def test_audio_templates_are_not_given_resolution_suffix(tmp_path):
    opts = ytdl.build_audio_opts("mp3", tmp_path, {"cookies_source": "none"})
    assert "%(height)sp" not in opts["outtmpl"]


def test_quality_and_container_are_in_batch_row(monkeypatch, tmp_path):
    monkeypatch.setattr("ytgrab.multi.shutil.get_terminal_size", lambda *a: type("S", (), {"columns": 120, "lines": 30})())
    job = Job(1, "https://youtu.be/a", "Kamata", "video", "1080", {"merge_output_format": "mkv"})
    downloader = MultiDownloader([job], workers=1)
    row = downloader._row(job)
    assert "1080p MKV" in row and "Kamata" in row


def test_new_video_container_default_is_mp4():
    assert config.DEFAULTS["video_container"] == "mp4"
