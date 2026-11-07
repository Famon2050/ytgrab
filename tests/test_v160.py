"""Tests for 1.6.0: bar styles, `dupes`, `find`, SponsorBlock levels and chapter splitting."""
import io
import json
import os
import sys
from pathlib import Path

import pytest

from ytgrab import config, dupes, finder, library, main, pending, progress, settings, subs, theme, ytdl
from ytgrab.progress import STYLE_ORDER, STYLES, ProgressRenderer, visible_len


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


@pytest.fixture
def trash(tmp_path, monkeypatch):
    """A private freedesktop trash; never touches the real one."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(dupes.shutil, "which", lambda name: None)       # no `gio`
    monkeypatch.setattr(sys, "platform", "linux")
    return tmp_path / "xdg" / "Trash"


def _write(path, data=b"x", size=None):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if size is None else (data * size)[:size])
    return path


# ===================================================================== bar styles
@pytest.mark.parametrize("color", [True, False])
@pytest.mark.parametrize("style", STYLE_ORDER)
@pytest.mark.parametrize("phase,known,shown", [
    ("wait", False, 0.0), ("download", True, 0.0), ("download", True, 0.55),
    ("download", True, 0.999), ("processing", True, 0.99), ("done", True, 1.0)])
def test_every_style_fits_every_window(monkeypatch, color, style, phase, known, shown):
    monkeypatch.setattr(theme, "_COLOR", color)
    r = ProgressRenderer(style=style)
    for cols in list(range(1, 80)) + [120, 300]:
        for t in (0.0, 0.7, 3.3):
            line = r._compose(cols, t, shown, known, phase)
            assert visible_len(line) <= max(cols - 1, 0), (style, cols, phase, visible_len(line))
            assert "\n" not in line and "\r" not in line


def test_styles_really_look_different(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    lines = {n: ProgressRenderer(style=n)._compose(80, 1.2, 0.5, True, "download") for n in STYLE_ORDER}
    assert len(set(lines.values())) == len(STYLE_ORDER)
    assert "━" in lines["minimal"] and "━" not in lines["classic"]


def test_unknown_style_falls_back_to_classic():
    assert ProgressRenderer(style="nope").style_name == "classic"
    assert ProgressRenderer().style_name == "classic"


def test_config_style_reaches_the_renderer(tmp_path):
    for name in STYLE_ORDER:
        o = ytdl.build_video_opts("720", tmp_path, {"cookies_source": "none", "bar_style": name})
        assert ytdl._renderers(o)[0].style_name == name
    a = ytdl.build_audio_opts("mp3", tmp_path, {"cookies_source": "none", "bar_style": "ocean"})
    assert ytdl._renderers(a)[0].style_name == "ocean"


def test_style_animation_moves_over_time(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    for name in ("classic", "fire", "ocean", "aurora", "neon", "matrix"):
        r = ProgressRenderer(style=name)
        assert r._compose(60, 0.2, 0.5, True, "download") != r._compose(60, 0.45, 0.5, True, "download"), name


def test_preview_prints_every_style_when_piped():
    out = io.StringIO()
    progress.preview_all(current="fire", stream=out, cols=90)
    lines = out.getvalue().splitlines()
    assert len(lines) == len(STYLE_ORDER)
    assert "●" in lines[STYLE_ORDER.index("fire")] and all("62%" in ln and "MB/s" in ln for ln in lines)
    assert all("\x1b" not in ln for ln in lines)


def test_preview_animates_on_a_terminal_and_restores_the_cursor(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)

    class Tty(io.StringIO):
        def isatty(self): return True
    out = Tty()
    monkeypatch.setattr(progress.time, "sleep", lambda s: None)
    progress.preview_all(stream=out, seconds=0.2, fps=10, cols=80)
    text = out.getvalue()
    assert text.startswith("\x1b[?25l") and text.endswith("\x1b[?25h")
    assert f"\x1b[{len(STYLE_ORDER)}A" in text                      # redraws in place


def test_bar_style_command_sets_and_validates(cdir, capsys):
    assert settings.cmd_bar_style("Fire") == 0
    assert config.load_config()["bar_style"] == "fire"
    assert settings.cmd_bar_style("sparkles") == 2
    assert config.load_config()["bar_style"] == "fire"


def test_settings_picker_saves_the_choice(cdir, monkeypatch):
    monkeypatch.setattr(settings, "preview_all", lambda **k: None)
    monkeypatch.setattr(settings, "render_table", lambda *a, **k: STYLE_ORDER.index("neon") + 1)
    cfg = config.load_config()
    assert settings.choose_bar_style(cfg) == "neon"
    assert config.load_config()["bar_style"] == "neon"


def test_default_style_is_classic():
    assert config.DEFAULTS["bar_style"] == "classic"


# ===================================================================== SponsorBlock + chapters
def _pps(opts):
    return {p["key"]: p for p in opts.get("postprocessors", [])}


@pytest.mark.parametrize("value,expect", [
    (False, None), (None, None), ("off", None), ("bogus", None),
    ("sponsors", ["sponsor"]), ("standard", ["sponsor", "selfpromo", "interaction"]),
    (True, ytdl.SPONSOR_CATS), ("all", ytdl.SPONSOR_CATS)])
def test_sponsorblock_levels(tmp_path, value, expect):
    cfg = {"cookies_source": "none", "sponsorblock": value}
    for opts in (ytdl.build_video_opts("720", tmp_path, cfg), ytdl.build_audio_opts("mp3", tmp_path, cfg)):
        pps = _pps(opts)
        if expect is None:
            assert "SponsorBlock" not in pps and "ModifyChapters" not in pps
        else:
            assert pps["SponsorBlock"]["categories"] == expect
            assert pps["ModifyChapters"]["remove_sponsor_segments"] == expect
            keys = [p["key"] for p in opts["postprocessors"]]
            assert keys.index("SponsorBlock") < keys.index("ModifyChapters")


def test_sponsor_level_reads_old_boolean_configs():
    assert ytdl.sponsor_level({"sponsorblock": True}) == "all"
    assert ytdl.sponsor_level({"sponsorblock": False}) == "off"
    assert ytdl.sponsor_level({}) == "off"
    assert ytdl.sponsor_level({"sponsorblock": "standard"}) == "standard"


@pytest.mark.parametrize("builder", ["video", "audio"])
def test_split_chapters_adds_the_postprocessor_and_a_chapter_template(tmp_path, builder):
    cfg = {"cookies_source": "none", "split_chapters": True}
    opts = (ytdl.build_video_opts("720", tmp_path, cfg) if builder == "video"
            else ytdl.build_audio_opts("mp3", tmp_path, cfg))
    assert "FFmpegSplitChapters" in _pps(opts)
    assert opts["postprocessors"][-1]["key"] == "FFmpegSplitChapters"       # after everything else
    t = opts["outtmpl"]
    expected = "%(title).150B%(height& {}p)s.%(ext)s" if builder == "video" else "%(title).150B.%(ext)s"
    assert t["default"].endswith(expected)
    assert "%(section_number)03d" in t["chapter"] and "%(section_title)" in t["chapter"]
    assert os.path.dirname(os.path.dirname(t["chapter"])) == os.path.dirname(t["default"])


def test_split_chapters_keeps_playlist_folders(tmp_path):
    tmpl = ytdl.playlist_template(tmp_path, "My List", 3)
    o = ytdl.build_video_opts("720", tmp_path, {"cookies_source": "none", "split_chapters": True}, outtmpl=tmpl)
    assert o["outtmpl"]["default"] == tmpl.replace(".%(ext)s", "%(height& {}p)s.%(ext)s", 1)
    assert os.path.dirname(os.path.dirname(o["outtmpl"]["chapter"])) == os.path.dirname(tmpl)


def test_chapters_off_by_default_leaves_a_plain_template(tmp_path):
    o = ytdl.build_video_opts("720", tmp_path, {"cookies_source": "none"})
    assert isinstance(o["outtmpl"], str) and "FFmpegSplitChapters" not in _pps(o)


def test_split_chapters_matches_what_yt_dlp_itself_generates():
    import yt_dlp
    real = [p for p in yt_dlp.parse_options(["--split-chapters"])[3]["postprocessors"]
            if p["key"] == "FFmpegSplitChapters"][0]
    mine = _pps(ytdl.build_video_opts("720", Path("/tmp"), {"cookies_source": "none", "split_chapters": True}))
    assert mine["FFmpegSplitChapters"] == real


def test_preset_keeps_sponsorblock_level_and_chapter_split():
    assert "split_chapters" in ytdl.PROFILE_KEYS and "sponsorblock" in ytdl.PROFILE_KEYS
    cfg = {"profiles": {}, "sponsorblock": "standard", "split_chapters": True, "last_kind": "audio",
           "last_video_quality": "720", "last_audio_format": "mp3", "last_sub_lang": "en",
           "subtitles_enabled": False, "speed_limit": ""}
    assert ytdl.save_profile(cfg, "podcast")[0]
    other = {"profiles": cfg["profiles"], "sponsorblock": False, "split_chapters": False}
    assert ytdl.apply_preset(other, "podcast", persist=False)
    assert other["sponsorblock"] == "standard" and other["split_chapters"] is True


def test_cli_flags_apply_to_this_run_only(cdir, monkeypatch):
    seen = []
    monkeypatch.setattr(main, "get_command", lambda cfg, urls: seen.append(dict(cfg)) or 0)
    monkeypatch.setattr(sys, "argv", ["ytgrab", "get", "https://youtu.be/x", "--sponsorblock", "standard", "--split-chapters"])
    with pytest.raises(SystemExit) as e:
        main._main()
    assert e.value.code == 0
    assert seen[0]["sponsorblock"] == "standard" and seen[0]["split_chapters"] is True
    assert config.load_config()["sponsorblock"] is False and not config.load_config()["split_chapters"]


def test_cli_flags_before_the_subcommand_work_too(cdir, monkeypatch):
    seen = []
    monkeypatch.setattr(main, "get_command", lambda cfg, urls: seen.append(dict(cfg)) or 0)
    monkeypatch.setattr(sys, "argv", ["ytgrab", "--sponsorblock", "sponsors", "get", "https://youtu.be/x"])
    with pytest.raises(SystemExit):
        main._main()
    assert seen[0]["sponsorblock"] == "sponsors"


def test_settings_sponsorblock_picker_stores_a_level(cdir, monkeypatch):
    answers = iter(["10", "0"])
    monkeypatch.setattr(settings, "prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr(settings, "banner", lambda *a, **k: None)
    monkeypatch.setattr(settings, "render_table", lambda title, rows, default_idx=None: 3)
    cfg = config.load_config()
    settings.settings_menu(cfg)
    assert cfg["sponsorblock"] == "standard"


# ===================================================================== dupes
def _hist(cdir, entries):
    (cdir / "history.json").write_text(json.dumps(entries))


def test_identical_copies_are_found_and_the_original_is_kept(tmp_path):
    a = _write(tmp_path / "Song.mp4", b"abc" * 1000)
    b = _write(tmp_path / "Song (1).mp4", b"abc" * 1000)
    groups, n = dupes.find_groups(tmp_path)
    assert n == 2 and len(groups) == 1
    g = groups[0]
    assert g.reason == "identical" and g.reclaimable == 3000
    assert {i.path for i in g.items} == {a, b}


def test_identical_content_with_different_names_is_still_identical(tmp_path):
    _write(tmp_path / "one.mp4", b"q" * 5000)
    _write(tmp_path / "completely different name.mp4", b"q" * 5000)
    (g,) = dupes.find_groups(tmp_path)[0]
    assert g.reason == "identical"


def test_same_video_downloaded_twice_in_different_quality(tmp_path, cdir):
    small = _write(tmp_path / "lowres" / "Clip.mp4", b"a", 1000)
    big = _write(tmp_path / "hires" / "Totally other name.mp4", b"b", 5000)
    _hist(cdir, [{"title": "Clip", "file": str(small), "key": "youtube AAAAAAAAAAA", "url": "u1"},
                 {"title": "Clip", "file": str(big), "key": "youtube AAAAAAAAAAA", "url": "u2"}])
    (g,) = dupes.find_groups(tmp_path)[0]
    assert g.reason == "same video"                       # linked by the history, not by the names
    assert g.items[g.keep].path == big and g.reclaimable == 1000


def test_old_history_entries_without_a_key_still_pair_up(tmp_path, cdir):
    a = _write(tmp_path / "x" / "One.mp4", b"a", 1000)
    b = _write(tmp_path / "y" / "Two.mp4", b"b", 2000)
    _hist(cdir, [{"title": "t", "file": str(a), "url": "https://youtu.be/dQw4w9WgXcQ"},
                 {"title": "t", "file": str(b), "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=9"}])
    (g,) = dupes.find_groups(tmp_path)[0]
    assert g.reason == "same video"


def test_same_title_after_ignoring_numbers_and_copy_suffixes(tmp_path):
    _write(tmp_path / "Playlist" / "003 - Lo-fi beats.mp3", b"a", 2000)
    _write(tmp_path / "Lo-fi beats.mp3", b"b", 3000)
    _write(tmp_path / "Lo-fi beats (2).mp3", b"c", 2500)
    (g,) = dupes.find_groups(tmp_path)[0]
    assert g.reason == "same title" and len(g.items) == 3
    assert g.items[g.keep].size == 3000                                 # biggest is suggested
    assert g.reclaimable == 4500


def test_chapter_files_of_different_videos_are_not_duplicates(tmp_path):
    for vid, size in (("Video A", 3000), ("Video B", 4000)):
        _write(tmp_path / f"{vid}.mp4", b"v", size)
        _write(tmp_path / vid / "001 - Intro.mp4", vid.encode(), 500 + size)
        _write(tmp_path / vid / "002 - Outro.mp4", vid.encode(), 700 + size)
    assert dupes.find_groups(tmp_path)[0] == []


def test_audio_and_video_of_the_same_title_are_not_duplicates(tmp_path):
    _write(tmp_path / "Track.mp4", b"v", 4000)
    _write(tmp_path / "Track.mp3", b"a", 900)
    assert dupes.find_groups(tmp_path)[0] == []


def test_unrelated_files_partials_and_thumbnails_are_ignored(tmp_path):
    _write(tmp_path / "A video.mp4", b"1", 3000)
    _write(tmp_path / "Another one.mp4", b"2", 3000)
    _write(tmp_path / "A video.mp4.part", b"1", 3000)
    _write(tmp_path / "A video.jpg", b"1", 3000)
    _write(tmp_path / ".hidden" / "A video.mp4", b"1", 3000)
    groups, n = dupes.find_groups(tmp_path)
    assert groups == [] and n == 2


def test_history_file_is_preferred_when_sizes_tie(tmp_path, cdir):
    a = _write(tmp_path / "z-copy.mp4", b"same" * 500)
    b = _write(tmp_path / "a-copy.mp4", b"same" * 500)
    _hist(cdir, [{"title": "t", "file": str(a), "key": "youtube AAAAAAAAAAA", "url": "u"}])
    (g,) = dupes.find_groups(tmp_path)[0]
    assert g.items[g.keep].path == a


def test_apply_plan_moves_to_trash_and_retargets_history(tmp_path, cdir, trash):
    keep = _write(tmp_path / "Song.mp4", b"abc" * 1000)
    dup = _write(tmp_path / "Song (1).mp4", b"abc" * 1000)
    _hist(cdir, [{"title": "Song", "file": str(dup), "key": "youtube AAAAAAAAAAA", "url": "u"}])
    (g,) = dupes.find_groups(tmp_path)[0]
    g.keep = [i.path for i in g.items].index(keep)
    removed, freed, problems = dupes.apply_plan([(g, g.keep)])
    assert (removed, freed, problems) == (1, 3000, [])
    assert keep.exists() and not dup.exists()
    assert (trash / "files" / "Song (1).mp4").exists()
    info = (trash / "info" / "Song (1).mp4.trashinfo").read_text()
    assert info.startswith("[Trash Info]") and "Song%20%281%29.mp4" in info and "DeletionDate=" in info
    assert config.load_history()[0]["file"] == str(keep)                # history follows the kept file


def test_trash_names_never_collide(tmp_path, trash):
    for i in range(3):
        f = _write(tmp_path / f"d{i}" / "same.mp4", b"x")
        assert dupes.move_to_trash(f)
    assert len(list((trash / "files").iterdir())) == 3


def test_full_hash_check_saves_files_that_only_look_identical(tmp_path, cdir, trash):
    """Same size and same first/middle/last MiB, different bytes in between."""
    size = 4 * 1024 * 1024
    data = bytearray(os_urandom(size))
    a = _write(tmp_path / "a.mp4", bytes(data))
    data[1_250_000] ^= 0xFF
    b = _write(tmp_path / "b.mp4", bytes(data))
    (g,) = dupes.find_groups(tmp_path)[0]
    assert g.reason == "identical"                                      # the cheap fingerprint is fooled
    removed, freed, problems = dupes.apply_plan([(g, 0)])
    assert removed == 0 and a.exists() and b.exists()
    assert problems and "left alone" in problems[0]


def os_urandom(n):
    return os.urandom(n)


def test_dry_run_changes_nothing(tmp_path, cdir, trash, capsys):
    _write(tmp_path / "x.mp4", b"abc" * 1000); _write(tmp_path / "x (1).mp4", b"abc" * 1000)
    assert dupes.dupes_command({"download_dir": str(tmp_path)}, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "identical copies" in out and "nothing was changed" in out
    assert len(list(tmp_path.glob("*.mp4"))) == 2


def test_auto_only_removes_verified_identical_copies(tmp_path, cdir, trash, capsys):
    _write(tmp_path / "x.mp4", b"abc" * 1000); _write(tmp_path / "x (1).mp4", b"abc" * 1000)
    _write(tmp_path / "Other.mp3", b"1", 2000); _write(tmp_path / "Other (1).mp3", b"2", 2500)   # same title only
    assert dupes.dupes_command({"download_dir": str(tmp_path)}, auto=True) == 0
    assert len(list(tmp_path.glob("x*.mp4"))) == 1
    assert len(list(tmp_path.glob("Other*.mp3"))) == 2                  # never auto-deleted
    assert "Moved 1 file(s) to the trash" in capsys.readouterr().out


def test_no_duplicates_and_bad_folder(tmp_path, capsys):
    _write(tmp_path / "only.mp4", b"1", 100)
    assert dupes.dupes_command({"download_dir": str(tmp_path)}) == 0
    assert "No duplicates found" in capsys.readouterr().out
    assert dupes.dupes_command({"download_dir": str(tmp_path / "missing")}) == 2


def test_interactive_review_keep_the_one_you_pick(tmp_path, cdir, trash, monkeypatch):
    a = _write(tmp_path / "Song.mp3", b"a", 3000)
    b = _write(tmp_path / "Song (1).mp3", b"b", 2000)
    answers = iter(["r", "2", ])                                        # review, keep file #2
    monkeypatch.setattr(dupes, "prompt", lambda *a_, **k: next(answers))
    monkeypatch.setattr(dupes, "ask_yes_no", lambda *a_, **k: True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    keep_second = sorted([a, b])[1]
    assert dupes.dupes_command({"download_dir": str(tmp_path)}) == 0
    survivors = list(tmp_path.glob("*.mp3"))
    assert survivors == [keep_second]


def test_interactive_skip_and_decline_leave_everything(tmp_path, cdir, trash, monkeypatch):
    _write(tmp_path / "Song.mp3", b"a", 3000); _write(tmp_path / "Song (1).mp3", b"b", 2000)
    answers = iter(["r", "", ])
    monkeypatch.setattr(dupes, "prompt", lambda *a_, **k: next(answers))
    monkeypatch.setattr(dupes, "ask_yes_no", lambda *a_, **k: False)    # "move to trash?" -> no
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert dupes.dupes_command({"download_dir": str(tmp_path)}) == 0
    assert len(list(tmp_path.glob("*.mp3"))) == 2


def test_dupes_cli_wiring(cdir, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(main, "dupes_command", lambda cfg, **k: seen.append(k) or 0)
    monkeypatch.setattr(sys, "argv", ["ytgrab", "dupes", "--dry-run", "--dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        main._main()
    assert seen == [{"dry_run": True, "auto": False, "directory": str(tmp_path)}]


# ===================================================================== find
H = [
    {"title": "Kendrick Lamar - HUMBLE.", "channel": "Kendrick Lamar", "kind": "video", "size": 5_000_000,
     "date": "2026-09-01 10:00", "url": "https://youtu.be/AAAAAAAAAAA", "file": ""},
    {"title": "Best of 2026 mix", "channel": "DJ Kendrick fan", "kind": "audio", "size": 1_000_000,
     "date": "2026-10-01 10:00", "url": "https://youtu.be/BBBBBBBBBBB", "file": ""},
    {"title": "Beyoncé - Halo", "channel": "Beyoncé", "kind": "video", "size": 3_000_000,
     "date": "2026-08-01 10:00", "url": "https://youtu.be/CCCCCCCCCCC", "file": ""},
]


@pytest.fixture
def lib(tmp_path):
    files = []
    for h, name in zip(H, ["humble.mp4", "mix.mp3", "halo.mp4"]):
        f = _write(tmp_path / name, b"x", 100)
        h["file"] = str(f)
        files.append(f)
    yield tmp_path
    for h in H:
        h["file"] = ""


def test_all_words_must_match_and_title_beats_channel(lib):
    hits = finder.search("kendrick", str(lib), history=H)
    assert [h.title for h in hits] == ["Kendrick Lamar - HUMBLE.", "Best of 2026 mix"]
    assert [h.title for h in finder.search("kendrick humble", str(lib), history=H)] == ["Kendrick Lamar - HUMBLE."]
    assert finder.search("kendrick halo", str(lib), history=H) == []
    assert finder.search("   ", str(lib), history=H) == []


def test_search_ignores_accents_and_case(lib):
    assert [h.title for h in finder.search("BEYONCE halo", str(lib), history=H)] == ["Beyoncé - Halo"]
    assert [h.title for h in finder.search("beyoncé", str(lib), history=H)] == ["Beyoncé - Halo"]


def test_kind_filter_and_limit(lib):
    assert [h.kind for h in finder.search("kendrick", str(lib), history=H, kind="audio")] == ["audio"]
    assert len(finder.search("kendrick", str(lib), history=H, limit=1)) == 1


def test_files_missing_from_disk_are_flagged(lib):
    Path(H[0]["file"]).unlink()
    hit = finder.search("humble", str(lib), history=H)[0]
    assert hit.exists is False and hit.title.startswith("Kendrick")


def test_files_not_in_history_are_found_by_name(lib, tmp_path):
    _write(tmp_path / "Playlist" / "004 - Lofi Girl study beats.mp3", b"x", 50)
    hits = finder.search("lofi study", str(tmp_path), history=H)
    assert len(hits) == 1 and hits[0].channel == "" and hits[0].kind == "audio" and hits[0].exists
    # files already covered by the history are not listed twice
    assert len(finder.search("humble", str(tmp_path), history=H)) == 1


def test_find_command_prints_the_location(lib, capsys, monkeypatch):
    monkeypatch.setattr(finder._config, "load_history", lambda: H)
    cfg = {"download_dir": str(lib)}
    assert finder.find_command(cfg, ["kendrick", "humble"], interactive=False) == 0
    out = capsys.readouterr().out
    assert "HUMBLE" in out and "humble.mp4" in out
    assert finder.find_command(cfg, ["zzzz"], interactive=False) == 1
    assert "Nothing found" in capsys.readouterr().out
    assert finder.find_command(cfg, [], interactive=False) == 2


def test_find_command_opens_files_and_folders(lib, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(finder, "open_path", lambda p: opened.append(Path(p)))
    answers = iter(["1", "f1", "9", "x", ""])
    monkeypatch.setattr(finder, "prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr(finder._config, "load_history", lambda: H)
    assert finder.find_command({"download_dir": str(lib)}, "humble", interactive=True) == 0
    assert opened == [Path(H[0]["file"]), Path(H[0]["file"]).parent]
    assert "Type 1-1" in capsys.readouterr().out


def test_cannot_open_a_file_that_is_gone(lib, monkeypatch, capsys):
    Path(H[0]["file"]).unlink()
    opened = []
    monkeypatch.setattr(finder, "open_path", lambda p: opened.append(p))
    answers = iter(["1", ""])
    monkeypatch.setattr(finder, "prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr(finder._config, "load_history", lambda: H)
    finder.find_command({"download_dir": str(lib)}, "humble", interactive=True)
    assert opened == [] and "no longer there" in capsys.readouterr().out


def test_find_cli_wiring(cdir, monkeypatch):
    seen = []
    monkeypatch.setattr(main, "find_command", lambda cfg, words, **k: seen.append((words, k)) or 0)
    monkeypatch.setattr(sys, "argv", ["ytgrab", "find", "kendrick", "lamar", "--audio", "-n", "5"])
    with pytest.raises(SystemExit):
        main._main()
    assert seen == [(["kendrick", "lamar"], {"kind": "audio", "limit": 5})]


def test_main_menu_has_find_and_dupes_shortcuts(cdir, monkeypatch):
    called = []
    monkeypatch.setattr(main, "find_command", lambda cfg, words, **k: called.append(("find", words)))
    monkeypatch.setattr(main, "dupes_command", lambda cfg, **k: called.append(("dupes",)))
    answers = iter(["kendrick", "", ""])
    monkeypatch.setattr(main, "prompt", lambda *a, **k: next(answers))
    cfg = config.load_config()
    assert main._handle_input(cfg, "f") is cfg
    assert main._handle_input(cfg, "dupes") is cfg
    assert called == [("find", "kendrick"), ("dupes",)]
