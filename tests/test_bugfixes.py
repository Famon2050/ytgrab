from ytgrab import config, library, ytdl
from ytgrab.flows import _build_jobs
from ytgrab.multi import JobRunner


def test_build_jobs_deduplicates_new_keys_within_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "ARCHIVE_FILE", tmp_path / "archive.txt")
    cfg = dict(config.DEFAULTS, download_dir=str(tmp_path), cookies_source="none")
    info = {"id": "same", "title": "Same", "extractor_key": "Youtube"}
    jobs = _build_jobs([info, dict(info)], ["url-1", "url-2"], "video", "720", {}, cfg,
                       use_archive=True)
    assert [j.url for j in jobs] == ["url-1"]


def test_get_filepath_prefers_final_postprocessed_filepath(tmp_path):
    final = tmp_path / "merged.mp4"
    component = tmp_path / "video.f137.mp4"
    info = {"filepath": str(final), "requested_downloads": [{"filepath": str(component)}]}
    assert ytdl.get_filepath(info) == final


def test_multi_result_prefers_final_postprocessed_filepath(tmp_path):
    final = tmp_path / "converted.mp3"
    component = tmp_path / "source.webm"
    info = {"filepath": str(final), "requested_downloads": [{"filepath": str(component)}]}
    assert JobRunner._result_of(info) == (str(final), 0)


def test_iter_media_ignores_unrecognized_document_extensions(tmp_path):
    media = tmp_path / "video.mp4"
    doc = tmp_path / "notes.pdf"
    media.write_bytes(b"video")
    doc.write_bytes(b"same bytes")
    assert [p.name for p, _, _ in library.iter_media(tmp_path)] == ["video.mp4"]


def test_title_scan_ignores_subtitles_and_documents(tmp_path):
    subtitle = tmp_path / "A title.ass"
    subtitle.write_text("subtitle")
    (tmp_path / "A title.pdf").write_text("document")
    assert library.scan_download_dir(tmp_path, "A title") is None

    media = tmp_path / "A title.mp4"
    media.write_bytes(b"video")
    assert library.scan_download_dir(tmp_path, "A title") == media


def test_existing_file_and_sibling_lookup_require_media_extensions(tmp_path):
    subtitle = tmp_path / "A title.ass"
    subtitle.write_text("subtitle")
    assert library._existing_file(subtitle) is None
    assert library._existing_file(tmp_path / "A title.webm") is None

    media = tmp_path / "A title.m4a"
    media.write_bytes(b"audio")
    assert library._existing_file(tmp_path / "A title.webm") == media
