"""Milestone 1 (1.11.0): the big banner logo and the refined updater panel."""
import io
import os
import re
import signal
import threading
import time
from types import SimpleNamespace

import pytest

from ytgrab import theme, updater_ui as ui
from ytgrab.updater_ui import FakeInstaller, UpdateView, clean_line, run_installer

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def plain(s):
    return ANSI.sub("", s)


class Tty(io.StringIO):
    def isatty(self):
        return True


# ===================================================================== big banner
@pytest.mark.parametrize("word", ["YTGRAB", "STREAM", "YTSTREAM"])
@pytest.mark.parametrize("name,sx,sy", theme.LOGO_SIZES)
@pytest.mark.parametrize("color", [True, False])
def test_logo_rows_have_the_promised_size(monkeypatch, word, name, sx, sy, color):
    monkeypatch.setattr(theme, "_COLOR", color)
    rows = theme.render_logo(word, sx, sy)
    cols, nrows = theme.logo_dims(word, sx, sy, shadow=color)
    assert len(rows) == nrows
    assert {theme._vis(r) if hasattr(theme, "_vis") else len(plain(r)) for r in rows} == {cols}


def test_shadow_only_exists_in_colour(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    colour = "".join(theme.render_logo("YTGRAB", 2, 2))
    assert "\x1b[48;2;" in colour                                   # a shadow colour behind half-blocks
    monkeypatch.setattr(theme, "_COLOR", False)
    mono = "".join(theme.render_logo("YTGRAB", 2, 2))
    assert "\x1b" not in mono and set(mono) <= set(" █▀▄")          # plain characters only


def test_letters_are_really_drawn(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", False)
    rows = theme.render_logo("T", 2, 2, shadow=False)
    assert rows[0].count("█") >= 8                                   # the T's bar
    assert rows[-1].count("█") + rows[-1].count("▄") + rows[-1].count("▀") <= 6    # the stem is narrower than the bar


def test_unknown_character_is_a_blank_not_a_crash(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", False)
    assert theme.render_logo("A?", 2, 1)


@pytest.mark.parametrize("cols,max_rows,expect", [
    (200, 28, "xl"), (116, 22, "xl"), (116, 8, "wide"), (100, 18, "large"), (80, 8, "wide"),
    (79, 40, None), (200, 7, None)])
def test_biggest_logo_that_fits(monkeypatch, cols, max_rows, expect):
    monkeypatch.setattr(theme, "_COLOR", True)
    pick = theme.pick_logo_size("YTGRAB", cols, max_rows)
    assert (pick[0] if pick else None) == expect


def _banner(monkeypatch, capsys, cols, lines, **kw):
    monkeypatch.setattr(theme.__dict__.get("shutil", __import__("shutil")), "get_terminal_size",
                        lambda fb=(80, 24): os.terminal_size((cols, lines)))
    cfg = {"download_dir": "/home/x/Downloads", "banner_size": "large"}
    theme.banner(cfg, clear=False, **kw)
    return plain(capsys.readouterr().out).splitlines()


@pytest.mark.parametrize("cols,lines", [(200, 50), (120, 40), (100, 30), (80, 24)])
def test_big_banner_fits_the_window_and_keeps_its_frame(monkeypatch, capsys, cols, lines):
    monkeypatch.setattr(theme, "_COLOR", True)
    out = _banner(monkeypatch, capsys, cols, lines)
    framed = [l for l in out if l and l[0] in "╭│╰"]
    assert framed[0].startswith("╭") and framed[-1].startswith("╰")
    assert len({len(l) for l in framed}) == 1                       # every frame row the same width
    assert max(len(l) for l in out) <= cols - 1
    assert any("youtube downloader" in l and "fakrudin" in l for l in out) and any("saving to" in l for l in out)


def test_bigger_window_gets_a_bigger_logo(monkeypatch, capsys):
    monkeypatch.setattr(theme, "_COLOR", True)
    small = _banner(monkeypatch, capsys, 80, 24)
    big = _banner(monkeypatch, capsys, 200, 60)
    assert len(big) > len(small) and max(map(len, big)) > max(map(len, small))


def test_banner_leaves_room_for_the_menu_on_short_windows(monkeypatch, capsys):
    monkeypatch.setattr(theme, "_COLOR", True)
    assert len(_banner(monkeypatch, capsys, 200, 26)) <= 12          # a 26-line window must not be eaten by the logo


def test_stream_banner_variant(monkeypatch, capsys):
    monkeypatch.setattr(theme, "_COLOR", True)
    out = _banner(monkeypatch, capsys, 120, 40, word="STREAM", tagline="stream YouTube in VLC")
    assert any("stream YouTube in VLC" in l for l in out)


# ===================================================================== updater panel
@pytest.mark.parametrize("color", [True, False])
def test_panel_lines_never_exceed_the_window(monkeypatch, color):
    monkeypatch.setattr(theme, "_COLOR", color)
    v = UpdateView("Installing ytgrab")
    v.feed("Collecting yt-dlp>=2024.1.1 from some/very/long/path " + "x" * 200)
    for cols in list(range(1, 120)) + [200]:
        for line in v.render(cols, now=v._t0 + 3.3, advance=False):
            assert len(plain(line)) <= max(cols - 1, 1), (cols, len(plain(line)))


def test_stages_move_forward_with_the_installer_output():
    v = UpdateView()
    seen = []
    for line in ("Processing /src/ytgrab", "Collecting yt-dlp", "  Downloading yt_dlp-1.whl (3 MB)",
                 "  Building wheel for ytgrab (pyproject.toml) ... done", "Attempting uninstall: ytgrab",
                 "Installing collected packages: ytgrab", "Successfully installed ytgrab-2.0"):
        v.feed(line)
        seen.append((v.floor, v.label))
    floors = [f for f, _ in seen]
    assert floors == sorted(floors) and len(set(floors)) == len(floors)
    assert [l for _, l in seen] == ["Preparing", "Resolving packages", "Downloading", "Building",
                                    "Replacing the old version", "Installing", "Finishing"]


def test_stages_never_go_backwards_when_lines_repeat():
    v = UpdateView()
    v.feed("Installing collected packages: a")
    v.feed("Collecting something-late")
    assert v.label == "Installing"


def test_bar_never_claims_more_than_the_installer_reported():
    t = [0.0]
    v = UpdateView(clock_fn=lambda: t[0])
    v.feed("Collecting x")
    shown = []
    for i in range(1, 400):
        t[0] = i * 0.1
        v.render(100, now=t[0])
        shown.append(v.shown)
    assert shown == sorted(shown)                                   # monotonic
    assert max(shown) <= 0.18 + 0.10 + 0.01                         # stage floor + the small allowed creep
    v.done = True
    for i in range(400, 460):
        t[0] = i * 0.1
        v.render(100, now=t[0])
    assert v.shown > 0.9


def test_install_output_is_captured_not_printed():
    out = Tty()
    res = run_installer(["x"], interactive=True, out=out, popen=lambda cmd, **kw: FakeInstaller(delay=0.01))
    assert res.ok and res.rc == 0 and len(res.tail) == len(ui.DEMO_SCRIPT)
    text = out.getvalue()
    assert text.startswith("\x1b[?25l") and text.rstrip().endswith("\x1b[?25h") or "\x1b[?25h" in text
    assert "Successfully installed ytgrab-1.11.0" in res.tail[-1]
    # the panel is wiped at the end: the last cursor-up/erase leaves nothing behind
    assert "\x1b[2A\r\x1b[J" in text


def test_failed_install_reports_rc_and_keeps_the_log():
    res = run_installer(["x"], interactive=True, out=Tty(),
                        popen=lambda cmd, **kw: FakeInstaller(("Collecting x", "ERROR: boom"), 0.01, returncode=7))
    assert not res.ok and res.rc == 7 and res.tail[-1] == "ERROR: boom" and not res.aborted


def test_single_ctrl_c_does_not_interrupt_the_install():
    killer = threading.Timer(0.15, lambda: os.kill(os.getpid(), signal.SIGINT))
    killer.start()
    res = run_installer(["x"], interactive=True, out=Tty(),
                        popen=lambda cmd, **kw: FakeInstaller(("Collecting a", "Collecting b", "Collecting c"), 0.2))
    killer.join()
    assert res.ok and not res.aborted
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler    # normal behaviour restored


def test_double_ctrl_c_aborts_safely():
    for delay in (0.15, 0.30):
        threading.Timer(delay, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
    res = run_installer(["x"], interactive=True, out=Tty(),
                        popen=lambda cmd, **kw: FakeInstaller(tuple(f"Collecting p{i}" for i in range(40)), 0.2))
    assert res.aborted and not res.ok
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_non_interactive_runs_leave_the_installer_output_alone():
    seen = {}
    def popen(cmd, **kw):
        seen.update(kw)
        return SimpleNamespace(wait=lambda: 4)
    res = run_installer(["x"], interactive=False, popen=popen)
    assert res.rc == 4 and seen == {}                              # no pipes, no environment changes


def test_installer_environment_is_quiet():
    seen = {}
    def popen(cmd, **kw):
        seen.update(kw)
        return FakeInstaller(("Collecting x",), 0.01)
    run_installer(["x"], interactive=True, out=Tty(), popen=popen)
    env = seen["env"]
    assert env["PIP_PROGRESS_BAR"] == "off" and env["NO_COLOR"] == "1" and env["PYTHONUNBUFFERED"] == "1"


def test_carriage_return_progress_lines_are_split():
    r, w = os.pipe()
    os.write(w, b"Downloading 10%\rDownloading 55%\rDownloading 100%\nDone\n")
    os.close(w)
    stream = os.fdopen(r, "rb", buffering=0)
    assert list(ui._iter_lines(stream)) == ["Downloading 10%", "Downloading 55%", "Downloading 100%", "Done"]


def test_clean_line_strips_colour_codes():
    assert clean_line("\x1b[1m\x1b[32mSuccessfully installed\x1b[0m  x\t") == "Successfully installed  x"


def test_cursor_is_always_restored(monkeypatch):
    out = Tty()
    v = UpdateView(out=out)
    v.start()
    time.sleep(0.15)
    v.stop(ok=False)
    assert out.getvalue().count("\x1b[?25l") == 1 and out.getvalue().endswith("\x1b[?25h")
