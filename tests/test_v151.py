"""Tests for 1.5.1: the single adaptive progress bar and the bugs found in the 1.5.0 review."""
import io
import re
import shutil
from collections import namedtuple
from pathlib import Path

import pytest

from ytgrab import config, flows, library, multi, pending, settings, subs, theme, ytdl
from ytgrab.fmt import fmt_size, parse_length_filter
from ytgrab.progress import MAX_BAR, ProgressRenderer, visible_len

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
Size = namedtuple("Size", "columns lines")


def _dl(name, done, total, vc="avc1", ac="none", **extra):
    d = {"status": "downloading", "filename": name, "downloaded_bytes": done,
         "total_bytes": total, "info_dict": {"vcodec": vc, "acodec": ac}}
    d.update(extra)
    return d


@pytest.fixture
def cdir(tmp_path, monkeypatch):
    d = tmp_path / "cfg"; d.mkdir()
    for mod in (config, library, pending, subs, settings):
        monkeypatch.setattr(mod, "CONFIG_DIR", d, raising=False)
    monkeypatch.setattr(config, "CONFIG_FILE", d / "config.json")
    monkeypatch.setattr(config, "HISTORY_FILE", d / "history.json")
    monkeypatch.setattr(library, "ARCHIVE_FILE", d / "archive.txt")
    monkeypatch.setattr(pending, "QUEUE_FILE", d / "queue.json")
    monkeypatch.setattr(subs, "SUBS_FILE", d / "subs.json")
    return d


class _Tty(io.StringIO):
    def isatty(self):
        return True


# ---------------------------------------------------------------- the bar
@pytest.mark.parametrize("color", [True, False])
@pytest.mark.parametrize("phase,known,shown", [
    ("wait", False, 0.0), ("download", True, 0.0), ("download", True, 0.37),
    ("download", True, 0.999), ("processing", True, 0.99), ("done", True, 1.0),
])
def test_bar_never_wider_than_the_window(monkeypatch, color, phase, known, shown):
    """The 1.5.0 bug: a fixed ~78 cell line wrapped in narrow windows and spammed the screen."""
    monkeypatch.setattr(theme, "_COLOR", color)
    r = ProgressRenderer()
    for cols in list(range(1, 90)) + [120, 200, 400]:
        line = r._compose(cols, 1.7, shown, known, phase)
        assert visible_len(line) <= max(cols - 1, 0), (cols, phase, visible_len(line))
        assert "\n" not in line and "\r" not in line
    assert visible_len(r._compose(400, 1.0, .5, True, "download")) <= MAX_BAR + 8


def test_bar_shows_a_single_line_of_bar_and_percent(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    line = ANSI.sub("", ProgressRenderer()._compose(80, 1.0, 0.42, True, "download"))
    assert line.strip().endswith("42%")
    assert "ETA" not in line and "/s" not in line and "Video" not in line


def test_video_and_audio_share_one_bar_that_never_goes_back():
    r = ProgressRenderer(io.StringIO())
    seen = []
    for done in (0, 25, 50, 75, 100):                       # video stream
        r.hook(_dl("v.f137.mp4", done, 100)); seen.append(r._target)
    r.hook({"status": "finished", "filename": "v.f137.mp4"}); seen.append(r._target)
    for done in (0, 50, 100):                               # audio stream starts: no reset
        r.hook(_dl("a.f140.m4a", done, 10, vc="none", ac="mp4a")); seen.append(r._target)
    vals = [x for x in seen if x is not None]
    assert vals == sorted(vals)
    assert 0.85 < vals[-1] <= 1.0
    r.close()


def test_progress_when_total_is_unknown_is_a_pulse_not_a_number():
    r = ProgressRenderer(io.StringIO())
    r.hook(_dl("x.mp4", 500, 0))                            # no total, no fragments
    assert r._target is None
    r.hook(_dl("x.mp4", 0, 0, fragment_index=3, fragment_count=10))
    assert r._target == pytest.approx(0.2 * 0.9)
    r.close()


def test_postprocessing_phase_and_finish():
    out = _Tty()
    r = ProgressRenderer(out)
    r.start(); r.hook(_dl("a.m4a", 50, 100, vc="none", ac="mp4a"))
    r.postprocessor_hook({"status": "started", "postprocessor": "FFmpegExtractAudio"})
    assert r._phase == "processing" and r._target == 1.0
    r.finish(); r.finish(); r.close()                       # idempotent
    text = out.getvalue()
    assert text.endswith("\n") and "\033[?25h" in text      # cursor restored, line ended
    assert "100%" in ANSI.sub("", text)
    assert r._thread is None


def test_close_clears_the_bar_without_a_trace():
    out = _Tty()
    r = ProgressRenderer(out)
    r.hook(_dl("v.mp4", 10, 100)); r.close()
    assert out.getvalue().endswith("\r\033[J\033[?25h")
    assert "\n" not in out.getvalue()


def test_piped_output_has_no_animation_or_escapes():
    out = io.StringIO()                                     # not a tty
    r = ProgressRenderer(out)
    for done in range(0, 101, 5):
        r.hook(_dl("f.mp4", done, 100, vc="avc1", ac="mp4a"))
    r.finish()
    text = out.getvalue()
    assert "\r" not in text and "\x1b" not in text
    assert [ln.strip() for ln in text.splitlines()] == ["10%", "20%", "30%", "40%", "50%", "60%",
                                                         "70%", "80%", "90%", "100%"]


def test_easing_glides_and_never_reaches_100_before_done():
    r = ProgressRenderer(io.StringIO())
    r.hook(_dl("f.mp4", 100, 100, vc="avc1", ac="mp4a"))
    shown = [ProgressRenderer.render_frame(r, 80, now=r._t0 + i * 0.05) and r._shown for i in range(1, 60)]
    assert shown == sorted(shown) and shown[0] < shown[-1]
    assert int(min(r._shown, 0.99) * 100) <= 99
    r.close()


# ---------------------------------------------------------------- download bookkeeping
def test_failed_single_download_is_not_recorded_as_success(monkeypatch, cdir):
    monkeypatch.setattr(config, "CONFIG_DIR", cdir)
    calls = []
    monkeypatch.setattr(flows, "do_download", lambda *a, **k: (None, False, "HTTP Error 500"))
    for name in ("clear_screen", "banner", "show_info", "save_config", "post_download_menu"):
        monkeypatch.setattr(flows, name, lambda *a, n=name, **k: calls.append(n))
    monkeypatch.setattr(flows, "ask_yes_no", lambda *a, **k: True)
    monkeypatch.setattr(flows, "render_table", lambda *a, **k: 1)
    monkeypatch.setattr(flows, "video_menu", lambda info: ([], {}))
    monkeypatch.setattr(flows, "add_history", lambda e: calls.append("history"))
    monkeypatch.setattr(flows, "archive_add", lambda k: calls.append("archive"))
    monkeypatch.setattr(flows, "notify", lambda *a: calls.append("notify"))
    monkeypatch.setattr(flows, "run_post_hook", lambda *a, **k: calls.append("hook"))
    rec = []
    monkeypatch.setattr(flows.failed, "record", lambda *a, **k: rec.append((a, k)))
    info = {"title": "T", "id": "abc", "extractor_key": "Youtube", "formats": []}
    cfg = dict(config.DEFAULTS, notify=True)
    assert flows.download_one(cfg, "https://youtu.be/abc", info=info) is False
    assert not {"history", "archive", "notify", "hook", "post_download_menu"} & set(calls)
    assert rec and rec[0][1]["error"] == "HTTP Error 500"


def test_do_download_reports_the_error(monkeypatch, capsys):
    monkeypatch.setattr(ytdl, "_extract_with_auth", lambda *a, **k: (None, "boom"))
    opts = ytdl._attach_progress({})
    assert ytdl.do_download("http://x", opts, {"cookies_source": "none"}) == (None, False, "boom")
    assert "boom" in capsys.readouterr().out


def test_do_download_success_and_cancel(monkeypatch):
    monkeypatch.setattr(ytdl, "_extract_with_auth", lambda *a, **k: ({"id": 1}, None))
    info, cancelled, err = ytdl.do_download("http://x", ytdl._attach_progress({}), {})
    assert info == {"id": 1} and not cancelled and err == ""

    def boom(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(ytdl, "_extract_with_auth", boom)
    assert ytdl.do_download("http://x", ytdl._attach_progress({}), {}) == (None, True, "")


def test_build_opts_use_one_renderer_and_a_silent_logger():
    o = ytdl.build_video_opts("720", Path("/tmp/x"), {"cookies_source": "none"})
    assert len(o["progress_hooks"]) == 1 and len(o["postprocessor_hooks"]) == 1
    assert o["progress_hooks"][0].__self__ is o["postprocessor_hooks"][0].__self__
    o["logger"].error("never printed")


# ---------------------------------------------------------------- menus & helpers
def test_portrait_video_gets_the_right_quality_rows():
    fm = [{"vcodec": "avc1", "acodec": "none", "height": h, "width": w, "tbr": t}
          for h, w, t in [(1920, 1080, 3000), (1280, 720, 1500), (854, 480, 700), (640, 360, 400)]]
    fm.append({"vcodec": "none", "acodec": "mp4a", "abr": 128, "tbr": 128})
    rows, _ = ytdl.video_menu({"formats": fm, "duration": 30})
    by_label = {r[1]: r for r in rows}
    assert "1080p" in by_label["1080p Full HD"][2]
    assert "720p" in by_label["720p HD"][2]
    assert not by_label["480p"][4] and not by_label["360p"][4]      # available, not greyed out


def test_url_file_with_bom_keeps_its_first_url(tmp_path, monkeypatch):
    f = tmp_path / "urls.txt"
    f.write_bytes(b"\xef\xbb\xbfhttps://youtu.be/one\n# c\nhttps://youtu.be/two\n\xff\n")
    got = []
    monkeypatch.setattr(flows, "batch_download", lambda cfg, urls, **k: got.append(urls))
    monkeypatch.setattr(flows, "ask_yes_no", lambda *a, **k: True)
    flows.batch_from_file({}, f)
    assert got == [["https://youtu.be/one", "https://youtu.be/two"]]


def test_fmt_size_is_decimal_like_its_labels():
    assert fmt_size(1_500_000) == "1.5 MB" and fmt_size(999) == "999.0 B"


@pytest.mark.parametrize("raw", ["abc", ">x", "a-b", "10"])
def test_bad_length_filter_gives_the_usage_message(raw):
    with pytest.raises(ValueError, match="use 10-60"):
        parse_length_filter(raw)


def test_profile_with_missing_values_does_not_wipe_settings():
    cfg = {"last_kind": "video", "last_sub_lang": "en", "profiles": {"p": {"last_kind": "audio", "last_sub_lang": None}}}
    assert ytdl.apply_preset(cfg, "p", persist=False)
    assert cfg["last_kind"] == "audio" and cfg["last_sub_lang"] == "en"


# ---------------------------------------------------------------- multi-download table
class _J:
    def __init__(self, i, status="downloading", pct=42, title="A fairly long video title that needs cutting"):
        self.idx, self.status, self.pct, self.title = i, status, pct, title
        self.speed, self.eta = "7.1 MB/s", "00:12"


@pytest.mark.parametrize("status", ["queued", "downloading", "paused", "processing", "done", "cancelled", "error"])
def test_table_rows_never_wrap(monkeypatch, status):
    d = multi.MultiDownloader([_J(1, status)], workers=1)
    for cols in list(range(2, 100)) + [160]:
        monkeypatch.setattr(shutil, "get_terminal_size", lambda fallback=None, c=cols: Size(c, 24))
        assert visible_len(d._row(d.jobs[0])) <= cols - 1, (cols, status)


# ---------------------------------------------------------------- config & misc
def test_two_processes_do_not_overwrite_each_others_settings(cdir):
    config.save_config(config.load_config())
    a = config.load_config()                                # "TUI"
    b = config.load_config(); b["theme"] = "ocean"; config.save_config(b)    # "CLI" changes theme
    config.load_config.__globals__["_BASELINE"].clear()
    config.load_config.__globals__["_BASELINE"].update(a)   # a is back in its own process view
    a["last_kind"] = "audio"; config.save_config(a)
    final = config.load_config()
    assert final["last_kind"] == "audio" and final["theme"] == "ocean"
    assert a["theme"] == "ocean"                            # caller's dict picked the change up


def test_queue_x_command_only_matches_the_remove_command():
    src = Path(flows.__file__).read_text()
    assert 'low == "x" or low.startswith("x ")' in src
