"""Tests for 1.7.0: speed + ETA on the bar, search with times, multi-select downloads."""
import io
import re

import pytest

from ytgrab import config, flows, search, settings, theme, ytdl
from ytgrab.progress import STYLE_ORDER, ProgressRenderer, visible_len

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _dl(name, done, total, speed=None, vc="avc1", ac="mp4a"):
    return {"status": "downloading", "filename": name, "downloaded_bytes": done, "total_bytes": total,
            "speed": speed, "info_dict": {"vcodec": vc, "acodec": ac}}


class Clock:
    def __init__(self): self.t = 100.0
    def __call__(self): return self.t


# ===================================================================== speed + ETA
@pytest.mark.parametrize("color", [True, False])
@pytest.mark.parametrize("style", STYLE_ORDER)
@pytest.mark.parametrize("phase", ["wait", "download", "processing", "done"])
def test_bar_with_speed_and_eta_still_fits_every_window(monkeypatch, color, style, phase):
    monkeypatch.setattr(theme, "_COLOR", color)
    r = ProgressRenderer(style=style)
    for cols in list(range(1, 100)) + [140, 400]:
        line = r._compose(cols, 1.3, 0.42, True, phase, speed="7.4 MB/s", eta="1:23", elapsed="0:12")
        assert visible_len(line) <= max(cols - 1, 0), (style, cols, phase, visible_len(line))
        assert "\n" not in line and "\r" not in line


def test_details_appear_when_there_is_room_and_drop_out_gracefully(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    r = ProgressRenderer()
    def plain(cols): return ANSI.sub("", r._compose(cols, 1.0, 0.42, True, "download", speed="7.4 MB/s", eta="1:23"))
    wide = plain(120)
    assert "42%" in wide and "7.4 MB/s" in wide and "ETA" in wide and "1:23" in wide
    mid = plain(40)                                           # ETA goes first...
    assert "7.4 MB/s" in mid and "ETA" not in mid
    narrow = plain(30)                                        # ...then the speed
    assert "MB/s" not in narrow and "42%" in narrow
    assert "42%" in plain(12)


def test_details_can_be_switched_off(monkeypatch):
    out = io.StringIO()
    r = ProgressRenderer(out, details=False)
    r.hook(_dl("a.mp4", 50, 100, speed=2e6))
    assert r._details() == (None, None, None)
    on = ProgressRenderer(out)
    on.hook(_dl("a.mp4", 50, 100, speed=2e6))
    assert on._details()[0] == "2.0 MB/s"


def test_config_flag_reaches_the_renderer(tmp_path):
    for flag in (True, False):
        o = ytdl.build_video_opts("720", tmp_path, {"cookies_source": "none", "show_speed_eta": flag})
        assert ytdl._renderers(o)[0].details is flag
    o = ytdl.build_audio_opts("mp3", tmp_path, {"cookies_source": "none"})
    assert ytdl._renderers(o)[0].details is True                 # on by default
    assert config.DEFAULTS["show_speed_eta"] is True


def test_speed_is_smoothed_and_formatted():
    clk = Clock()
    r = ProgressRenderer(io.StringIO()); r._clock = clk
    r.hook(_dl("a.mp4", 10, 1000, speed=1_000_000))
    r.hook(_dl("a.mp4", 20, 1000, speed=2_000_000))
    assert r._speed == pytest.approx(1_300_000)                  # 0.7 * old + 0.3 * new
    assert r._details()[0] == "1.3 MB/s"
    clk.t += 10                                                  # no data for a while: do not show a stale speed
    assert r._details()[0] is None


def test_eta_follows_the_overall_pace_of_video_and_audio():
    clk = Clock()
    r = ProgressRenderer(io.StringIO()); r._clock = clk
    for i in range(0, 11):                                       # 10 % every second on one stream
        clk.t = 100 + i
        r.hook(_dl("a.mp4", i * 100, 1000, speed=1e6))
    # at 100 % - 0... use a mid-way sample instead
    r2 = ProgressRenderer(io.StringIO()); r2._clock = clk
    for i in range(0, 6):
        clk.t = 200 + i
        r2.hook(_dl("a.mp4", i * 100, 1000, speed=1e6))          # 50 % after 5 s
    assert r2._eta() == pytest.approx(5.0, abs=0.6)
    clk.t += 30
    assert r2._eta() is None                                     # stalled: no confident ETA


def test_eta_needs_data_first():
    r = ProgressRenderer(io.StringIO())
    assert r._eta() is None
    r.hook(_dl("a.mp4", 10, 100, speed=1e6))
    assert r._eta() is None


@pytest.mark.parametrize("sec,txt", [(None, "--:--"), (-1, "--:--"), (0, "0:00"), (59.6, "1:00"),
                                     (223, "3:43"), (3725, "1:02:05"), (10 ** 7, "--:--")])
def test_clock_format(sec, txt):
    assert ProgressRenderer.fmt_clock(sec) == txt


def test_finished_bar_reports_the_time_it_took(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    r = ProgressRenderer()
    line = ANSI.sub("", r._compose(100, 1.0, 1.0, True, "done", elapsed="0:42"))
    assert line.rstrip().endswith("100%  in 0:42")


def test_settings_toggle_for_speed_and_eta(cdir_v170, monkeypatch):
    answers = iter(["28", "0"])
    monkeypatch.setattr(settings, "prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr(settings, "banner", lambda *a, **k: None)
    monkeypatch.setattr(settings, "ask_yes_no", lambda *a, **k: False)
    cfg = config.load_config()
    settings.settings_menu(cfg)
    assert cfg["show_speed_eta"] is False


@pytest.fixture
def cdir_v170(tmp_path, monkeypatch):
    d = tmp_path / "cfg"; d.mkdir()
    monkeypatch.setattr(config, "CONFIG_DIR", d)
    monkeypatch.setattr(config, "CONFIG_FILE", d / "config.json")
    monkeypatch.setattr(config, "HISTORY_FILE", d / "history.json")
    return d


# ===================================================================== selecting several results
@pytest.mark.parametrize("raw,n,expect,problems", [
    ("2", 10, [2], []),
    ("1,2,6,7", 10, [1, 2, 6, 7], []),
    ("1 2 6 7", 10, [1, 2, 6, 7], []),
    ("1, 2 ,6;7", 10, [1, 2, 6, 7], []),
    ("3-5", 10, [3, 4, 5], []),
    ("1,3-5,9", 10, [1, 3, 4, 5, 9], []),
    ("5-3", 10, [5, 4, 3], []),                              # backwards range
    ("7,1,7,1,3", 10, [7, 1, 3], []),                        # typed order kept, repeats dropped
    ("all", 4, [1, 2, 3, 4], []),
    ("ALL", 3, [1, 2, 3], []),
    ("*", 2, [1, 2], []),
    ("2–4", 10, [2, 3, 4], []),                              # en dash
    ("", 10, [], []),
])
def test_parse_selection(raw, n, expect, problems):
    assert search.parse_selection(raw, n) == (expect, problems)


def test_parse_selection_reports_what_it_could_not_use():
    nums, bad = search.parse_selection("1,2,99,x,8-12", 10)
    assert nums == [1, 2, 8, 9, 10]
    assert any("99" in b for b in bad) and any("x" in b for b in bad) and any("8-12" in b for b in bad)
    assert search.parse_selection("0,11", 10)[0] == []


RES = [{"title": f"Video number {i} with a rather long title that keeps going and going", "url": f"https://youtu.be/v{i}",
        "id": f"v{i}", "uploader": "Some Channel Name", "duration": d, "views": 1000 * i}
       for i, d in enumerate([220, 3725, 59, 0], 1)]


# ===================================================================== search table with times
def test_results_show_title_and_time_side_by_side(monkeypatch, capsys):
    monkeypatch.setattr(search.shutil, "get_terminal_size", lambda fb=None: type("S", (), {"columns": 110, "lines": 30})())
    search.show_results(RES, "lofi")
    out = ANSI.sub("", capsys.readouterr().out)
    assert "Time" in out and "3:40" in out and "1:02:05" in out and "0:59" in out
    row1 = [ln for ln in out.splitlines() if ln.strip().startswith("1 ")][0]
    assert row1.index("3:40") < row1.index("Some Channel")       # time right after the title
    assert any(" ?" in ln for ln in out.splitlines() if ln.strip().startswith("4 "))   # unknown length
    assert "min:sec" in out


@pytest.mark.parametrize("cols", [40, 50, 66, 80, 100, 140, 220])
def test_results_rows_never_wrap(monkeypatch, capsys, cols):
    monkeypatch.setattr(search.shutil, "get_terminal_size", lambda fb=None: type("S", (), {"columns": cols, "lines": 30})())
    search.show_results(RES, "q")
    rows = [ln for ln in ANSI.sub("", capsys.readouterr().out).splitlines() if ln.strip() and ln.strip()[0].isdigit()]
    assert rows and all(len(ln) <= cols - 1 for ln in rows), (cols, max(len(r) for r in rows))
    assert all(any(t in ln for t in ("3:40", "1:02:05", "0:59", " ?")) for ln in rows)    # time is never dropped


# ===================================================================== multi-select flow
def _flow(monkeypatch, typed, results=RES):
    answers = iter(typed)
    monkeypatch.setattr(search, "prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr(search, "search_youtube", lambda *a, **k: list(results))
    calls = {"one": [], "batch": []}
    monkeypatch.setattr(flows, "download_one", lambda cfg, url, **k: calls["one"].append(url))
    monkeypatch.setattr(flows, "batch_download",
                        lambda cfg, urls, **k: calls["batch"].append((cfg, list(urls), k)) or (len(urls), 0))
    return calls


def test_one_number_still_uses_the_normal_single_download(monkeypatch):
    calls = _flow(monkeypatch, ["2"])
    assert search.search_flow({}, "q") is True
    assert calls["one"] == ["https://youtu.be/v2"] and calls["batch"] == []


def test_several_numbers_download_them_all_at_once(monkeypatch, capsys):
    calls = _flow(monkeypatch, ["1,2,3", ""])                  # picks, then "press enter"
    monkeypatch.setattr(search, "ask_yes_no", lambda *a, **k: True)
    tables = iter([1, 3])                                      # Video, then the 3rd quality row
    monkeypatch.setattr(search, "render_table", lambda title, rows, default_idx=None: next(tables))
    assert search.search_flow({"last_kind": "audio"}, "q") is True
    cfg, urls, kw = calls["batch"][0]
    assert urls == ["https://youtu.be/v1", "https://youtu.be/v2", "https://youtu.be/v3"]
    assert kw.get("use_archive") is True                      # already-downloaded videos are skipped, with their location
    assert cfg["last_kind"] == "video" and cfg["last_video_quality"] == ytdl.VIDEO_TARGETS[2][0]
    out = ANSI.sub("", capsys.readouterr().out)
    assert "Selected 3 videos" in out and "total time 1:06:44" in out and "3 downloaded" in out
    assert calls["one"] == []


def test_audio_choice_for_a_selection(monkeypatch):
    calls = _flow(monkeypatch, ["1,2", ""])
    monkeypatch.setattr(search, "ask_yes_no", lambda *a, **k: True)
    tables = iter([2, 2])
    monkeypatch.setattr(search, "render_table", lambda title, rows, default_idx=None: next(tables))
    base = {"last_kind": "video", "last_video_quality": "1080"}
    search.search_flow(base, "q")
    cfg = calls["batch"][0][0]
    assert cfg["last_kind"] == "audio" and cfg["last_audio_format"] == ytdl.AUDIO_FORMATS[1][0]
    assert base["last_kind"] == "video"                        # your saved defaults are not changed


def test_all_selects_every_result(monkeypatch):
    calls = _flow(monkeypatch, ["all", ""])
    monkeypatch.setattr(search, "ask_yes_no", lambda *a, **k: True)
    monkeypatch.setattr(search, "render_table", lambda *a, **k: 1)
    search.search_flow({}, "q")
    assert len(calls["batch"][0][1]) == 4


def test_declining_the_confirmation_goes_back_to_the_list(monkeypatch):
    calls = _flow(monkeypatch, ["1,2", "3"])                   # decline, then pick a single one
    monkeypatch.setattr(search, "ask_yes_no", lambda *a, **k: False)
    assert search.search_flow({}, "q") is True
    assert calls["batch"] == [] and calls["one"] == ["https://youtu.be/v3"]


def test_bad_input_is_explained_and_the_good_part_is_used(monkeypatch, capsys):
    calls = _flow(monkeypatch, ["zzz", "2,99"])
    search.search_flow({}, "q")
    out = ANSI.sub("", capsys.readouterr().out)
    assert "Invalid choice" in out and "Ignored" in out and "99" in out
    assert calls["one"] == ["https://youtu.be/v2"]


def test_selection_works_on_the_filtered_list(monkeypatch):
    calls = _flow(monkeypatch, ["f", "all", ""])
    answers = iter(["f", ">1", "all", ""])
    monkeypatch.setattr(search, "prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr(search, "ask_yes_no", lambda *a, **k: True)
    monkeypatch.setattr(search, "render_table", lambda *a, **k: 1)
    search.search_flow({}, "q")
    assert [u.rsplit("/", 1)[1] for u in calls["batch"][0][1]] == ["v1", "v2"]   # the 59 s and unknown-length ones are filtered out


def test_cancel_and_refine_still_work(monkeypatch):
    calls = _flow(monkeypatch, [""])
    assert search.search_flow({}, "q") is False and calls["batch"] == [] and calls["one"] == []


def test_search_prompt_explains_the_multi_select(monkeypatch):
    seen = []
    answers = iter([""])
    monkeypatch.setattr(search, "prompt", lambda msg, **k: seen.append(msg) or next(answers))
    monkeypatch.setattr(search, "search_youtube", lambda *a, **k: list(RES))
    search.search_flow({}, "q")
    assert "1,3,6" in seen[0] and "all" in seen[0]
