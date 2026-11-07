"""Tests for 1.5.2: never download the same video twice without being told where it already is."""
import json
from pathlib import Path

import pytest

from ytgrab import config, flows, library, pending, settings, subs
from ytgrab.library import DupIndex, find_existing, scan_download_dir


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


INFO = {"id": "dQw4w9WgXcQ", "extractor_key": "Youtube", "title": "Never Gonna Give You Up",
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}


def _hist(cdir, **kw):
    entry = {"title": INFO["title"], "url": INFO["webpage_url"], "file": "", "date": "2026-10-01 12:00",
             "size": 5_000_000, "kind": "video"}
    entry.update(kw)
    (cdir / "history.json").write_text(json.dumps([entry]))


def test_downloaded_file_is_found_with_its_location(cdir, tmp_path):
    f = tmp_path / "dl" / "Never Gonna Give You Up.mp4"
    f.parent.mkdir(); f.write_bytes(b"x")
    _hist(cdir, file=str(f), key="youtube dQw4w9WgXcQ")
    d = find_existing(INFO, "https://youtu.be/dQw4w9WgXcQ", download_dir=tmp_path / "dl")
    assert d["state"] == "exists" and d["file"] == str(f) and d["date"] == "2026-10-01 12:00"


@pytest.mark.parametrize("url", [
    "https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1"])
def test_every_link_to_the_same_video_matches(cdir, tmp_path, url):
    f = tmp_path / "a.mp4"; f.write_bytes(b"x")
    _hist(cdir, file=str(f))                               # an OLD entry: no stored key
    d = find_existing(None, url, download_dir=tmp_path)
    assert d and d["state"] == "exists" and d["file"] == str(f)


def test_a_different_video_is_not_a_duplicate(cdir, tmp_path):
    f = tmp_path / "a.mp4"; f.write_bytes(b"x")
    _hist(cdir, file=str(f), key="youtube dQw4w9WgXcQ")
    other = dict(INFO, id="AAAAAAAAAAA", title="Something else entirely",
                 webpage_url="https://www.youtube.com/watch?v=AAAAAAAAAAA")
    assert find_existing(other, "https://youtu.be/AAAAAAAAAAA", download_dir=tmp_path) is None


def test_converted_file_with_a_new_extension_is_still_found(cdir, tmp_path):
    (tmp_path / "Never Gonna Give You Up.mkv").write_bytes(b"x")
    _hist(cdir, file=str(tmp_path / "Never Gonna Give You Up.mp4"), key="youtube dQw4w9WgXcQ")
    d = find_existing(INFO, "", download_dir=tmp_path)
    assert d["state"] == "exists" and d["file"].endswith(".mkv")


def test_file_that_was_moved_is_found_in_the_download_folder(cdir, tmp_path):
    (tmp_path / "Playlist").mkdir()
    moved = tmp_path / "Playlist" / "003 - Never Gonna Give You Up.mp4"; moved.write_bytes(b"x")
    _hist(cdir, file=str(tmp_path / "gone" / "Never Gonna Give You Up.mp4"), key="youtube dQw4w9WgXcQ")
    d = find_existing(INFO, "", download_dir=tmp_path)
    assert d["state"] == "exists" and d["file"] == str(moved)


def test_deleted_file_is_reported_missing_not_blocking(cdir, tmp_path):
    _hist(cdir, file=str(tmp_path / "gone.mp4"), key="youtube dQw4w9WgXcQ")
    d = find_existing(INFO, "", download_dir=tmp_path)
    assert d["state"] == "missing" and d["file"] == str(tmp_path / "gone.mp4")


def test_same_title_without_history_is_flagged_as_same_name_only(cdir, tmp_path):
    (tmp_path / "Never Gonna Give You Up.webm").write_bytes(b"x")
    d = find_existing(INFO, "", download_dir=tmp_path)
    assert d["state"] == "same-name"
    assert scan_download_dir(tmp_path, "Never Gonna Give You Up").name.endswith(".webm")
    (tmp_path / "Never Gonna Give You Up.webm").unlink()
    (tmp_path / "Never Gonna Give You Up.jpg").write_bytes(b"x")       # thumbnails don't count
    (tmp_path / "Never Gonna Give You Up.mp4.part").write_bytes(b"x")  # nor half-downloads
    assert scan_download_dir(tmp_path, "Never Gonna Give You Up") is None


def test_archive_only_memory_has_no_location(cdir, tmp_path):
    library.archive_add(["youtube dQw4w9WgXcQ"])
    d = find_existing(INFO, "", download_dir=tmp_path)
    assert d["state"] == "archived" and d["file"] == ""
    assert find_existing(dict(INFO, id="ZZZZZZZZZZZ", title="nope"), "", download_dir=tmp_path) is None


def test_index_is_built_once_for_big_batches(cdir, tmp_path):
    f = tmp_path / "a.mp4"; f.write_bytes(b"x")
    _hist(cdir, file=str(f), key="youtube dQw4w9WgXcQ")
    idx = DupIndex()
    for _ in range(3):
        assert find_existing(INFO, "", index=idx, scan=False)["state"] == "exists"


# ---------------------------------------------------------------- the interactive prompt
def _cfg(tmp_path):
    return dict(config.DEFAULTS, download_dir=str(tmp_path), use_archive=True)


def _answers(monkeypatch, *seq):
    it = iter(seq)
    monkeypatch.setattr(flows, "prompt", lambda *a, **k: next(it))


def test_duplicate_is_reported_with_location_and_skipped(cdir, tmp_path, monkeypatch, capsys):
    f = tmp_path / "v.mp4"; f.write_bytes(b"x" * 2000)
    _hist(cdir, file=str(f), key="youtube dQw4w9WgXcQ")
    _answers(monkeypatch, "")                                  # Enter = skip
    assert flows._check_duplicate(_cfg(tmp_path), INFO, "https://youtu.be/dQw4w9WgXcQ") is True
    out = capsys.readouterr().out
    assert "Already downloaded" in out and str(f) in out and "2026-10-01" in out


def test_open_folder_then_download_again_anyway(cdir, tmp_path, monkeypatch):
    f = tmp_path / "v.mp4"; f.write_bytes(b"x")
    _hist(cdir, file=str(f), key="youtube dQw4w9WgXcQ")
    opened = []
    monkeypatch.setattr(flows, "open_path", lambda p: opened.append(Path(p)))
    _answers(monkeypatch, "o", "f", "d")
    assert flows._check_duplicate(_cfg(tmp_path), INFO, "") is False
    assert opened == [f, tmp_path]


def test_missing_file_just_downloads_again_with_a_note(cdir, tmp_path, monkeypatch, capsys):
    _hist(cdir, file=str(tmp_path / "gone.mp4"), key="youtube dQw4w9WgXcQ")
    assert flows._check_duplicate(_cfg(tmp_path), INFO, "") is False
    assert "no longer at" in capsys.readouterr().out


def test_check_can_be_turned_off_and_never_blocks_on_errors(cdir, tmp_path, monkeypatch):
    f = tmp_path / "v.mp4"; f.write_bytes(b"x")
    _hist(cdir, file=str(f), key="youtube dQw4w9WgXcQ")
    assert flows._check_duplicate(dict(_cfg(tmp_path), use_archive=False), INFO, "") is False
    monkeypatch.setattr(flows, "find_existing", lambda *a, **k: 1 / 0)
    assert flows._check_duplicate(_cfg(tmp_path), INFO, "") is False


def test_download_one_does_not_download_a_duplicate(cdir, tmp_path, monkeypatch):
    f = tmp_path / "v.mp4"; f.write_bytes(b"x")
    _hist(cdir, file=str(f), key="youtube dQw4w9WgXcQ")
    _answers(monkeypatch, "")
    for name in ("clear_screen", "banner", "show_info"):
        monkeypatch.setattr(flows, name, lambda *a, **k: None)
    monkeypatch.setattr(flows, "do_download", lambda *a, **k: pytest.fail("downloaded a duplicate"))
    monkeypatch.setattr(flows, "ask_yes_no", lambda *a, **k: pytest.fail("asked to proceed"))
    assert flows.download_one(_cfg(tmp_path), "https://youtu.be/dQw4w9WgXcQ", info=INFO) is True


def test_new_history_entries_store_the_key(cdir, monkeypatch):
    class J:
        url, title, kind, quality, size, channel, filepath = "u", "t", "video", "720", 1, "c", "/x.mp4"
        archive_key = "youtube abc"
    monkeypatch.setattr(flows, "run_post_hook", lambda *a, **k: True)
    flows._record_done(dict(config.DEFAULTS, notify=False), J(), quiet=True)
    assert config.load_history()[-1]["key"] == "youtube abc"


def test_batch_skip_message_says_where_the_files_are(cdir, tmp_path, monkeypatch, capsys):
    f = tmp_path / "v.mp4"; f.write_bytes(b"x")
    _hist(cdir, file=str(f), key="youtube dQw4w9WgXcQ")
    library.archive_add(["youtube dQw4w9WgXcQ"])
    jobs = flows._build_jobs([INFO], ["https://youtu.be/dQw4w9WgXcQ"], "video", "720", {}, _cfg(tmp_path),
                             use_archive=True)
    out = capsys.readouterr().out
    assert jobs == [] and "1 already downloaded, skipped" in out and str(f) in out
