import re

import pytest

from ytgrab import config, theme

ANSI = re.compile(r"\x1b\[[0-9;]*m|\x1b\][^\x07]*\x07")


def render(capsys, size, cols=80, theme_name="youtube", monkeypatch=None):
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback=(80, 24): __import__("os").terminal_size((cols, 24)))
    theme.apply_theme(theme_name)
    cfg = dict(config.DEFAULTS, download_dir="/home/me/Videos")
    theme.banner(cfg, size=size, clear=False)
    out = capsys.readouterr().out
    return [ANSI.sub("", ln) for ln in out.splitlines()]


@pytest.mark.parametrize("name", theme.THEME_ORDER)
@pytest.mark.parametrize("size", ["large", "compact"])
def test_framed_rows_are_aligned(capsys, monkeypatch, size, name):
    lines = [l for l in render(capsys, size, theme_name=name, monkeypatch=monkeypatch)
             if l and l[0] in "╭│╰"]
    assert lines and len({len(l) for l in lines}) == 1          # every row same width
    assert len(lines[0]) <= 80                              # never wider than the (80 column) terminal
    assert lines[0].startswith("╭") and lines[-1].startswith("╰")


def test_classic_contains_logo_and_credit(capsys, monkeypatch):
    text = "\n".join(render(capsys, "classic", monkeypatch=monkeypatch))
    assert "██╗" in text and "youtube downloader" in text and "fakrudin" in text
    assert "saving to" in text


def test_large_is_the_big_block_logo(capsys, monkeypatch):
    text = "\n".join(render(capsys, "large", cols=100, monkeypatch=monkeypatch))
    assert ("▀" in text or "█" in text) and "██╗" not in text
    assert "youtube downloader" in text and "fakrudin" in text and "saving to" in text


def test_minimal_is_one_colored_line(capsys, monkeypatch):
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback=(80, 24): __import__("os").terminal_size((80, 24)))
    theme.apply_theme("youtube")
    theme.banner(dict(config.DEFAULTS, download_dir="/x"), size="minimal", clear=False)
    raw = capsys.readouterr().out
    body = [l for l in raw.splitlines() if l.strip()]
    assert len(body) == 1 and "\x1b[38;2" in body[0]            # one line, coloured
    plain = ANSI.sub("", body[0])
    assert plain.startswith("▶ ytgrab") and "━" in plain and len(plain) <= theme.W


@pytest.mark.parametrize("cols,expect", [(100, "classic"), (57, "compact"), (45, "minimal")])
def test_falls_back_on_narrow_terminals(capsys, monkeypatch, cols, expect):
    lines = render(capsys, "classic", cols=cols, monkeypatch=monkeypatch)
    text = "\n".join(lines)
    assert max(len(l) for l in lines) <= cols
    assert ("██╗" in text) == (expect == "classic")
    assert (any(l.startswith("╭") for l in lines)) == (expect != "minimal")


def test_unknown_size_defaults_to_large(capsys, monkeypatch):
    lines = render(capsys, "bogus", cols=100, monkeypatch=monkeypatch)
    assert any(l.startswith("╭") for l in lines) and any("saving to" in l for l in lines)
    assert not any("██╗" in l for l in lines)


def test_interp_returns_reusable_tuples():
    cols = theme._interp((0, 0, 0), (255, 255, 255), 5)
    assert all(isinstance(c, tuple) and len(c) == 3 for c in cols)
    assert list(cols[2]) == list(cols[2])                       # not an exhausted generator
