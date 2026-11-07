"""Milestone 3 (1.13.0): ytstream, watching YouTube in VLC with a remembered position."""
import io
import re
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from ytgrab import config, home, main, stream, theme, ytstream
from ytgrab.stream import PlaySession, RcClient, StreamError

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
FAKE_VLC = str(Path(__file__).with_name("fakevlc.py"))


def plain(s):
    return ANSI.sub("", s)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(stream, "STREAM_FILE", tmp_path / "stream_history.json")
    return tmp_path


def fake_cfg(extra="", **kw):
    cfg = dict(config.DEFAULTS, stream_player=FAKE_VLC, stream_args=extra, stream_resume=True)
    cfg.update(kw)
    return cfg


def info(**kw):
    d = {"id": "abc123", "url": "https://youtu.be/abc123", "title": "Kamata (Official Video)",
         "channel": "Diamond Platnumz", "duration": 600, "is_live": False, "video_url": "http://v/1",
         "audio_url": "http://a/1", "user_agent": "UA/1.0", "height": 1080, "ext": "mp4"}
    d.update(kw)
    return d


class Keys:
    """Keyboard stand-in: returns the scripted keys one per read, then None."""

    def __init__(self, *tokens, delay=0.0):
        self.tokens, self.delay, self.closed = list(tokens), delay, False

    def read(self, timeout=0.1):
        time.sleep(max(self.delay, 0.01))
        return self.tokens.pop(0) if self.tokens else None

    def close(self):
        self.closed = True


# ===================================================================== choosing the stream
@pytest.mark.parametrize("q,expect", [
    ("best", "bestvideo*+bestaudio/best/best"),
    ("nonsense", "bestvideo*+bestaudio/best/best")])
def test_stream_format(q, expect):
    assert stream.stream_format(q) == expect


def test_stream_quality_cap_uses_short_edge_for_portrait_and_has_no_uncapped_fallback():
    selector = stream.stream_format("720")
    portrait_720 = {"format_id": "portrait720", "width": 720, "height": 1280,
                    "vcodec": "avc1", "acodec": "none", "fps": 30, "tbr": 2000}
    portrait_1080 = {"format_id": "portrait1080", "width": 1080, "height": 1920,
                     "vcodec": "avc1", "acodec": "none", "fps": 30, "tbr": 5000}
    landscape_360 = {"format_id": "landscape360", "width": 640, "height": 360,
                     "vcodec": "avc1", "acodec": "none", "fps": 30, "tbr": 800}
    audio = {"format_id": "audio", "vcodec": "none", "acodec": "mp4a", "abr": 128}

    selected = list(selector({"formats": [portrait_720, portrait_1080, landscape_360, audio]}))
    assert selected[0]["requested_formats"] == [portrait_720, audio]

    too_large_only = list(selector({"formats": [portrait_1080, audio]}))
    assert too_large_only == []


def test_stream_options():
    o = stream.stream_opts({"stream_quality": "720", "speed_limit": "1M"})
    assert o["noplaylist"] and o["skip_download"] and o["format_sort"][0] == "res:720"
    assert "vcodec:h264" in o["format_sort"] and "ratelimit" not in o       # a download speed limit must not slow playback
    assert "res:" not in " ".join(stream.stream_opts({"stream_quality": "best"})["format_sort"])
    assert stream.stream_opts({"stream_quality": "bogus"})["format_sort"][0] == "res:1080"
    o["logger"].error("never printed")


def test_separate_video_and_audio_streams():
    raw = {"id": "x", "title": "T", "uploader": "U", "duration": 90, "webpage_url": "https://youtu.be/x",
           "requested_formats": [
               {"url": "http://v", "vcodec": "avc1", "acodec": "none", "height": 1080, "http_headers": {"User-Agent": "UA"}},
               {"url": "http://a", "vcodec": "none", "acodec": "mp4a"}]}
    s = stream.streams_from_info(raw)
    assert (s["video_url"], s["audio_url"], s["height"], s["user_agent"]) == ("http://v", "http://a", 1080, "UA")
    assert s["title"] == "T" and s["channel"] == "U" and s["duration"] == 90 and not s["is_live"]


def test_audio_first_order_is_handled():
    raw = {"requested_formats": [{"url": "http://a", "vcodec": "none", "acodec": "opus"},
                                 {"url": "http://v", "vcodec": "vp9", "acodec": "none"}]}
    s = stream.streams_from_info(raw)
    assert (s["video_url"], s["audio_url"]) == ("http://v", "http://a")


def test_combined_stream_and_live():
    s = stream.streams_from_info({"url": "http://one", "title": "Combined", "height": 360})
    assert s["video_url"] == "http://one" and s["audio_url"] is None
    live = stream.streams_from_info({"url": "http://m3u8", "is_live": True, "title": "Live"})
    assert live["is_live"] and live["audio_url"] is None
    only = stream.streams_from_info({"requested_formats": [{"url": "http://both", "vcodec": "avc1", "acodec": "mp4a"}]})
    assert only["video_url"] == "http://both" and only["audio_url"] is None


def test_no_stream_is_an_error():
    with pytest.raises(StreamError):
        stream.streams_from_info({"title": "nothing playable"})


def test_resolve_paths():
    good = lambda url, opts, dl: ({"url": "http://one", "title": "T", "webpage_url": url}, None)
    assert stream.resolve("https://youtu.be/x", {}, extractor=good)["video_url"] == "http://one"
    with pytest.raises(StreamError, match="private"):
        stream.resolve("u", {}, extractor=lambda u, o, d: (None, "Video unavailable: private"))
    playlist = lambda u, o, d: ({"entries": [None, {"url": "http://first", "title": "First"}]}, None)
    assert stream.resolve("u", {}, extractor=playlist)["title"] == "First"
    with pytest.raises(StreamError, match="empty"):
        stream.resolve("u", {}, extractor=lambda u, o, d: ({"entries": []}, None))
    seen = {}
    stream.resolve("u", {"stream_quality": "480"}, extractor=lambda u, o, d: seen.update(o=o, d=d) or ({"url": "x"}, None))
    assert seen["d"] is False and seen["o"]["format_sort"][0] == "res:480"       # nothing is downloaded


# ===================================================================== VLC
def test_find_vlc(tmp_path, monkeypatch):
    exe = tmp_path / "myvlc"
    exe.write_text("#!/bin/sh\n")
    assert stream.find_vlc({"stream_player": str(exe)}) == str(exe)
    assert stream.find_vlc({"stream_player": str(tmp_path / "nope")}) is None          # a wrong custom path is not silently ignored
    monkeypatch.setattr(stream.shutil, "which", lambda n: "/usr/bin/vlc" if n == "vlc" else None)
    assert stream.find_vlc({}) == "/usr/bin/vlc"
    monkeypatch.setattr(stream.shutil, "which", lambda n: None)
    monkeypatch.setattr(stream, "_MAC_VLC", str(exe))
    assert stream.find_vlc({}) == str(exe)
    monkeypatch.setattr(stream, "_MAC_VLC", str(tmp_path / "missing"))
    assert stream.find_vlc({}) is None
    assert "vlc" in stream.install_hint().lower()


def test_vlc_command_line():
    cmd = stream.vlc_command("/usr/bin/vlc", info(), start=754, port=4321, fullscreen=True, extra="--aout=alsa --foo 'a b'")
    assert cmd[0] == "/usr/bin/vlc" and "--play-and-exit" in cmd
    assert "--meta-title=Kamata (Official Video)" in cmd                            # one argument, no shell quoting needed
    assert "--http-user-agent=UA/1.0" in cmd and "--start-time=754" in cmd and "--fullscreen" in cmd
    assert "--extraintf=rc" in cmd and "--rc-host=127.0.0.1:4321" in cmd
    assert "--aout=alsa" in cmd and "--foo" in cmd and "a b" in cmd                 # extra options split like a shell would
    assert cmd.index("http://v/1") < cmd.index("--input-slave=http://a/1")          # the audio rides along as a slave input


def test_vlc_command_variants():
    plain_cmd = stream.vlc_command("vlc", info(audio_url=None, user_agent=""), start=0, port=None)
    assert "--start-time" not in " ".join(plain_cmd) and "--extraintf=rc" not in plain_cmd
    assert not any(a.startswith(("--input-slave", "--http-user-agent", "--fullscreen")) for a in plain_cmd)
    live = stream.vlc_command("vlc", info(is_live=True), start=300)
    assert not any(a.startswith("--start-time") for a in live)                       # a live stream cannot start at 5:00
    weird = stream.vlc_command("vlc", info(title='He said "hi" & --quit; $(rm -rf /)'))
    assert weird[2] == '--meta-title=He said "hi" & --quit; $(rm -rf /)'             # one inert argument


def test_free_port_is_usable():
    p = stream.free_port()
    with socket.socket() as s:
        s.bind(("127.0.0.1", p))


# ===================================================================== remembered position
def test_history_roundtrip_and_order(store):
    stream.record(info(id="a", url="https://youtu.be/a", title="A"), position=30, duration=600, new_session=True)
    stream.record(info(id="b", url="https://youtu.be/b", title="B"), position=5, duration=100, new_session=True)
    assert [h["id"] for h in stream.load_history()] == ["b", "a"]                   # newest first
    again = stream.record(info(id="a", url="https://youtu.be/a", title="A"), position=90, new_session=True)
    assert again["count"] == 2 and again["position"] == 90 and again["duration"] == 600     # fields are kept, not wiped
    assert [h["id"] for h in stream.load_history()] == ["a", "b"]
    upd = stream.record(info(id="a", url="https://youtu.be/a"), position=120)         # an update is not a new session
    assert upd["count"] == 2 and stream.get("a")["position"] == 120 and stream.get("https://youtu.be/a")["id"] == "a"
    assert stream.get("nope") is None and stream.get(None) is None


def test_history_remove_clear_and_cap(store):
    for i in range(310):
        stream.record(info(id=f"v{i}", url=f"https://youtu.be/v{i}", title=f"V{i}"), position=1)
    hist = stream.load_history()
    assert len(hist) == stream.HISTORY_LIMIT and hist[0]["id"] == "v309"
    assert stream.remove("v309") is True and stream.remove("v309") is False
    stream.clear()
    assert stream.load_history() == []


def test_history_survives_a_damaged_file(store):
    (store / "stream_history.json").write_text("{ not json")
    assert stream.load_history() == []
    stream.record(info(), position=10)
    assert len(stream.load_history()) == 1


@pytest.mark.parametrize("entry,expect", [
    ({"position": 754, "duration": 2710}, 754),
    ({"position": 5, "duration": 2710}, 0),                                      # barely started
    ({"position": 15, "duration": 2710}, 15),
    ({"position": 2600, "duration": 2710}, 0),                                   # 95 %+ counts as watched
    ({"position": 2000, "duration": 2710, "finished": True}, 0),
    ({"position": 500}, 500), ({}, 0), (None, 0)])
def test_resume_point(entry, expect):
    assert stream.resume_point(entry) == expect


def test_continue_list_and_finished_rules(store):
    stream.record(info(id="a", url="u1", title="A"), position=300, duration=600)
    stream.record(info(id="b", url="u2", title="B"), position=2, duration=600)
    stream.record(info(id="c", url="u3", title="C"), position=590, duration=600, finished=True)
    assert [h["id"] for h in stream.continue_list()] == ["a"]
    assert stream.is_finished(570, 600) and not stream.is_finished(500, 600)
    assert stream.is_finished(585, 600, ended_cleanly=True) and not stream.is_finished(500, 600, ended_cleanly=True)
    assert not stream.is_finished(100, 0)


@pytest.mark.parametrize("sec,txt", [(0, "0:00"), (59, "0:59"), (754, "12:34"), (3725, "1:02:05"), (None, "0:00"), (-5, "0:00")])
def test_clock(sec, txt):
    assert stream.clock(sec) == txt


# ===================================================================== VLC remote control
class RcServer:
    """What VLC's rc interface looks like on the wire."""

    def __init__(self, replies=None, banner=True):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.got, self.replies, self.banner = [], replies or {}, banner
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        conn, _ = self.srv.accept()
        if self.banner:
            conn.sendall(b"VLC media player 3.0.20 Vetinari\r\nCommand Line Interface initialized. Type `help' for help.\r\n> ")
        buf = b""
        while True:
            try:
                d = conn.recv(1024)
            except OSError:
                return
            if not d:
                return
            buf += d
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                cmd = line.strip().decode()
                self.got.append(cmd)
                if cmd == "quit":
                    conn.close()
                    return
                conn.sendall(self.replies.get(cmd.split()[0], "").encode() + b"> ")

    def close(self):
        self.srv.close()


def test_rc_reads_time_and_length():
    srv = RcServer({"get_time": "754\r\n", "get_length": "2710\r\n"})
    rc = RcClient(srv.port)
    assert rc.connect(timeout=3)
    assert rc.get_time() == 754 and rc.get_length() == 2710
    rc.pause(); rc.seek(100.7)
    rc.quit()
    time.sleep(0.1)
    assert srv.got == ["get_time", "get_length", "pause", "seek 100", "quit"]
    rc.close(); srv.close()


def test_rc_when_nothing_is_playing_or_vlc_is_gone():
    srv = RcServer({"get_time": "\r\n"})                                    # VLC answers with nothing while idle
    rc = RcClient(srv.port)
    assert rc.connect(timeout=3) and rc.get_time() is None
    rc.quit(); time.sleep(0.15)
    assert rc.get_time() is None                                            # the connection is gone: no crash
    assert RcClient(stream.free_port()).connect(timeout=0.4) is False        # nobody is listening
    assert RcClient(1).get_time() is None                                    # never connected


# ===================================================================== a VLC session (a stand-in VLC process)
def session_for(extra, start=0, port=None):
    port = port or stream.free_port()
    cmd = [sys.executable] + stream.vlc_command(FAKE_VLC, info(), start=start, port=port, extra=extra)
    return PlaySession(cmd, port, poll=0.1), port


def wait(cond, secs=6):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_session_tracks_the_position_until_vlc_closes():
    sess, _ = session_for("--fake-run=2")
    sess.start()
    assert wait(lambda: sess.pos is not None and sess.pos >= 1) and sess.rc_ok and sess.length == 600
    assert wait(lambda: not sess.running) and sess.returncode == 0
    last = sess.pos
    time.sleep(0.3)
    assert sess.pos == last and 1 <= last <= 3                                  # the last position stays available
    sess.stop()


def test_session_starts_where_asked_and_can_seek_and_pause():
    sess, _ = session_for("--fake-run=8", start=120)
    sess.start()
    assert wait(lambda: sess.pos is not None and sess.pos >= 120)
    sess.seek_by(+30)
    assert wait(lambda: sess.pos is not None and sess.pos >= 150)
    sess.seek_by(-1000)                                                          # never before the start
    assert wait(lambda: sess.pos is not None and sess.pos <= 3)
    sess.toggle_pause()
    assert sess.paused
    time.sleep(0.4)
    p = sess.pos
    time.sleep(0.8)
    assert abs((sess.pos or 0) - (p or 0)) <= 1                                  # paused: the position does not run on
    sess.stop()
    assert not sess.running


def test_session_without_remote_control_still_works():
    sess, _ = session_for("--fake-run=1 --fake-no-rc")
    sess.start()
    assert wait(lambda: not sess.running)
    assert sess.rc_ok is False and sess.pos is None
    sess.toggle_pause(); sess.seek_by(10)                                        # harmless without rc
    sess.stop()


def test_stop_closes_vlc_politely():
    sess, _ = session_for("--fake-run=60")
    sess.start()
    assert wait(lambda: sess.rc_ok)
    t0 = time.monotonic()
    sess.stop()
    assert not sess.running and time.monotonic() - t0 < 5


def test_stop_falls_back_to_terminate():
    sess, _ = session_for("--fake-run=60 --fake-no-rc")                         # cannot be asked nicely
    sess.start()
    time.sleep(0.3)
    sess.stop()
    assert not sess.running


# ===================================================================== playing one video
def resolver(**kw):
    return lambda url, cfg, quality=None: info(**kw)


class PythonVlc(PlaySession):
    """The fake VLC, run through this Python (so it needs no executable bit or shebang)."""

    def __init__(self, cmd, port):
        super().__init__([sys.executable] + cmd, port, poll=0.1)


def play(cfg, url="https://youtu.be/abc123", keys=None, auto_resume=False, asker=None, **kw):
    return ytstream.play_one(cfg, url, auto_resume=auto_resume, resolver=kw.pop("res", resolver()),
                             session_factory=kw.pop("session_factory", PythonVlc), reader=keys, out=io.StringIO(),
                             asker=asker or (lambda e, p: p), **kw)


def test_playing_remembers_the_last_minute(store):
    res = play(fake_cfg("--fake-run=60"), keys=Keys(*([None] * 120), "q", delay=0.02))
    assert res["quit"] and not res["finished"] and 1 <= res["position"] <= 20
    h = stream.get("abc123")
    assert h["position"] == res["position"] and h["duration"] == 600 and h["count"] == 1 and h["title"].startswith("Kamata")


def test_finishing_a_video_clears_the_resume_point(store):
    stream.record(info(), position=569, duration=600)                 # 1 s short of the 95 % "watched" line
    res = play(fake_cfg("--fake-run=3"), auto_resume=True, keys=None)
    assert res["finished"] is True
    h = stream.get("abc123")
    assert h["finished"] is True and h["position"] == 0 and stream.continue_list() == []


def test_next_time_it_resumes_from_there(store):
    stream.record(info(), position=300, duration=600)
    asked = []
    res = play(fake_cfg("--fake-run=2"), asker=lambda e, p: asked.append(p) or p)
    assert asked == [300] and res["position"] >= 300
    stream.record(info(), position=300, duration=600, finished=False)
    res = play(fake_cfg("--fake-run=2"), asker=lambda e, p: 0)                    # "start over"
    assert res["position"] < 10


def test_resume_prompt_cancel_and_automatic(store):
    stream.record(info(), position=300, duration=600)
    assert play(fake_cfg("--fake-run=1"), asker=lambda e, p: None) is None
    asked = []
    res = play(fake_cfg("--fake-run=1"), auto_resume=True, asker=lambda e, p: asked.append(1))
    assert asked == [] and res["position"] >= 300                                   # "Continue watching" never asks again
    stream.record(info(), position=300, duration=600, finished=False)
    cfg = fake_cfg("--fake-run=1", stream_resume=False)
    res = play(cfg, asker=lambda e, p: asked.append(1))
    assert asked == [] and res["position"] < 10                                     # resume switched off
    stream.record(info(), position=300, duration=600, finished=False)
    live = play(fake_cfg("--fake-run=1"), res=resolver(is_live=True), asker=lambda e, p: asked.append(1))
    assert asked == [] and live is not None                                          # a live stream has nothing to resume


def test_resume_answer_parsing(monkeypatch):
    entry = {"duration": 600}
    for typed, expect in (("", 300), ("r", 300), ("s", 0), ("start", 0), ("c", None), ("q", None), ("n", None)):
        monkeypatch.setattr(ytstream, "prompt", lambda *a, t=typed, **k: t)
        assert ytstream._ask_resume(entry, 300) == expect


def test_vlc_missing_is_explained(store, capsys):
    assert play({"stream_player": "/nope/vlc"}) is None
    out = capsys.readouterr().out
    assert "VLC was not found" in out and "Install it" in out


def test_stream_errors_are_explained(store, capsys):
    def bad(url, cfg, quality=None):
        raise StreamError("Video unavailable: private")
    assert play(fake_cfg(), res=bad) is None
    assert "Could not get a stream: Video unavailable: private" in capsys.readouterr().out

    def boom(url, cfg, quality=None):
        raise RuntimeError("extractor bug")
    assert play(fake_cfg(), res=boom) is None
    assert "extractor bug" in capsys.readouterr().out


def test_vlc_that_dies_at_once_is_reported(store, capsys):
    res = play(fake_cfg("--fake-exit=3"))
    assert res is not None and res["position"] == 0
    assert "closed straight away" in capsys.readouterr().out
    assert stream.get("abc123")["finished"] is False


def test_it_survives_without_position_tracking(store):
    res = play(fake_cfg("--fake-run=1 --fake-no-rc"))
    assert res["position"] == 0 and not res["finished"]


def test_extra_vlc_options_and_fullscreen_reach_vlc(store):
    seen = {}

    class Spy:
        def __init__(self, cmd, port):
            seen["cmd"] = cmd
            self.pos, self.length, self.rc_ok, self.paused, self.returncode, self.running = 5, 600, True, False, 0, False
        def start(self): return self
        def stop(self): pass
    play(fake_cfg("--aout=alsa", stream_fullscreen=True), session_factory=Spy)
    assert "--aout=alsa" in seen["cmd"] and "--fullscreen" in seen["cmd"]


# ===================================================================== the panel and its keys
class S:
    def __init__(self, pos=None, length=None, rc_ok=True, paused=False):
        self.pos, self.length, self.rc_ok, self.paused = pos, length, rc_ok, paused


@pytest.mark.parametrize("color", [True, False])
def test_panel_fits_every_window(monkeypatch, color):
    monkeypatch.setattr(theme, "_COLOR", color)
    for s in (S(754, 2710), S(None, None), S(30, None), S(754, 2710, paused=True), S(None, None, rc_ok=False)):
        for inf in (info(), info(is_live=True, channel="", height=None, title="T" * 300)):
            for cols in list(range(12, 120)) + [220]:
                lines = ytstream.render_now_playing(inf, s, cols, 1.3, started=0.0)
                assert len(lines) == 5 and all(theme._vis(l) <= cols - 1 for l in lines), (cols, s.pos)


def test_panel_content(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    out = plain("\n".join(ytstream.render_now_playing(info(), S(754, 2710), 120, 2.0, started=0.0)))
    assert "NOW PLAYING IN VLC" in out and "Kamata (Official Video)" in out and "Diamond Platnumz · 1080p" in out
    assert "12:34 / 45:10" in out and "27%" in out and "space pause" in out
    assert "PAUSED" in plain("\n".join(ytstream.render_now_playing(info(), S(1, 10, paused=True), 120, 0, started=0.0)))
    live = plain("\n".join(ytstream.render_now_playing(info(is_live=True, duration=600), S(95, None), 120, 0, started=0.0)))
    assert "LIVE" in live and "1:35" in live and "(live)" in live
    wait_ = plain("\n".join(ytstream.render_now_playing(info(), S(None, None, rc_ok=False), 120, 1.0, started=0.0)))
    assert "connecting to VLC" in wait_
    late = plain("\n".join(ytstream.render_now_playing(info(), S(None, None, rc_ok=False), 120, 30.0, started=0.0)))
    assert "unavailable" in late and "space pause" not in late and "q stop" in late


def test_panel_keys_control_vlc():
    calls = []

    class Fake:
        running = True
        def toggle_pause(self): calls.append("pause")
        def seek_by(self, d): calls.append(d)
        def stop(self):
            calls.append("stop"); Fake.running = False
    quit_ = ytstream._watch(Fake(), info(), {}, Keys(" ", "LEFT", "RIGHT", "q"), io.StringIO(), time.monotonic)
    assert quit_ is True and calls == ["pause", -10, 10, "stop"]


def test_panel_is_drawn_in_place_on_a_terminal(store):
    class Tty(io.StringIO):
        def isatty(self): return True
    out = Tty()
    ytstream.play_one(fake_cfg("--fake-run=1.5"), "https://youtu.be/abc123", resolver=resolver(), session_factory=PythonVlc,
                      reader=Keys(delay=0.05), out=out, asker=lambda e, p: p)
    text = out.getvalue()
    assert "\x1b[?25l" in text and "\x1b[?25h" in text and re.search(r"\x1b\[5A", text)
    assert "NOW PLAYING IN VLC" in plain(text)


# ===================================================================== the dashboard's options
def test_links_and_searches():
    for link in ("https://www.youtube.com/watch?v=abc", "youtu.be/abc", "https://m.youtube.com/watch?v=abc",
                 "HTTPS://WWW.YOUTUBE.COM/shorts/x"):
        assert ytstream.looks_like_link(link)
    for text in ("lofi hip hop", "youtube is great", "https://vimeo.com/1", ""):
        assert not ytstream.looks_like_link(text)
    assert ytstream.normalise_link("youtu.be/abc") == "https://youtu.be/abc"
    assert ytstream.normalise_link("https://youtu.be/abc") == "https://youtu.be/abc"


def test_handle_text_routes(monkeypatch):
    seen = []
    monkeypatch.setattr(ytstream, "play_one", lambda cfg, url, **k: seen.append(("play", url)))
    monkeypatch.setattr(ytstream, "search_and_play", lambda cfg, q: seen.append(("search", q)))
    ytstream.handle_text({}, "youtu.be/abc")
    ytstream.handle_text({}, "lofi mix")
    assert seen == [("play", "https://youtu.be/abc"), ("search", "lofi mix")]


RESULTS = [{"title": f"Video {i}", "url": f"https://youtu.be/{i}", "duration": 100 * i, "uploader": "U", "views": 10, "id": str(i)}
           for i in range(1, 6)]


def test_search_and_play(monkeypatch, capsys):
    played = []
    monkeypatch.setattr(ytstream, "search_youtube", lambda q, n=20, cfg=None: RESULTS)
    monkeypatch.setattr(ytstream, "play_queue", lambda cfg, picks, **k: played.append([p["id"] for p in picks]))
    for typed in (["2"], ["1,3,5"], ["zzz", "99", "4"], [""]):
        answers = iter(typed)
        monkeypatch.setattr(ytstream, "prompt", lambda *a, **k: next(answers))
        ytstream.search_and_play({}, "kendrick")
    assert played == [["2"], ["1", "3", "5"], ["4"]]
    out = capsys.readouterr().out
    assert "Type a number" in out and "Ignored" in out
    monkeypatch.setattr(ytstream, "search_youtube", lambda q, n=20, cfg=None: [])
    ytstream.search_and_play({}, "nothing")
    assert "No results" in capsys.readouterr().out


def test_queue_order_skip_and_stop(monkeypatch):
    order = []
    monkeypatch.setattr(ytstream, "play_one", lambda cfg, url, **k: order.append(url[-1]) or {"ok": 1})
    answers = iter(["", "s"])
    monkeypatch.setattr(ytstream, "prompt", lambda *a, **k: next(answers))
    ytstream.play_queue({}, RESULTS[:4])                  # 1 plays, Enter -> 2 plays, "s" -> 3 is skipped, 4 plays
    assert order == ["1", "2", "4"]
    order.clear()
    answers = iter(["q"])
    ytstream.play_queue({}, RESULTS[:3])
    assert order == ["1"]
    order.clear()
    monkeypatch.setattr(ytstream, "play_one", lambda cfg, url, **k: order.append(url[-1]) or None)    # the first one failed: stop
    ytstream.play_queue({}, RESULTS[:3])
    assert order == ["1"]


def test_continue_watching_lists_and_plays(store, monkeypatch, capsys):
    stream.record(info(id="a", url="https://youtu.be/a", title="Alpha"), position=300, duration=600)
    stream.record(info(id="b", url="https://youtu.be/b", title="Beta"), position=1, duration=600)
    played = []
    monkeypatch.setattr(ytstream, "play_one", lambda cfg, url, **k: played.append((url, k)))
    monkeypatch.setattr(ytstream, "prompt", lambda *a, **k: "1")
    ytstream.continue_watching({})
    out = plain(capsys.readouterr().out)
    assert "Alpha" in out and "Beta" not in out and "5:00 / 10:00" in out
    assert played == [("https://youtu.be/a", {"auto_resume": True})]
    stream.clear()
    ytstream.continue_watching({})
    assert "Nothing to continue" in capsys.readouterr().out


def test_history_menu(store, monkeypatch, capsys):
    for i, t in enumerate(("One", "Two", "Three")):
        stream.record(info(id=f"v{i}", url=f"https://youtu.be/v{i}", title=t), position=100, duration=600)
    answers = iter(["d 1", "nonsense", "c", ""])
    monkeypatch.setattr(ytstream, "prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr(ytstream, "ask_yes_no", lambda *a, **k: False)
    ytstream.history_menu({})
    assert [h["title"] for h in stream.load_history()] == ["Two", "One"]            # "Three" was newest = row 1 = deleted
    answers2 = iter(["c", ""])
    monkeypatch.setattr(ytstream, "ask_yes_no", lambda *a, **k: True)
    monkeypatch.setattr(ytstream, "prompt", lambda *a, **k: next(answers2))
    ytstream.history_menu({})
    assert stream.load_history() == []
    played = []
    stream.record(info(id="z", url="https://youtu.be/z", title="Z"), position=100)
    monkeypatch.setattr(ytstream, "play_one", lambda cfg, url, **k: played.append(url))
    monkeypatch.setattr(ytstream, "prompt", lambda *a, **k: "1")
    ytstream.history_menu({})
    assert played == ["https://youtu.be/z"]
    stream.clear()
    ytstream.history_menu({})
    assert "history is empty" in capsys.readouterr().out


def test_settings_menu(store, monkeypatch):
    cfg = dict(config.DEFAULTS)
    saved = []
    monkeypatch.setattr(ytstream, "save_config", lambda c: saved.append(dict(c)))
    picks = iter([1, 2, 3, 4, 5, 6, 7])
    monkeypatch.setattr(ytstream, "render_table",
                        lambda title, rows, default_idx=None: 4 if title == "Highest quality to stream" else next(picks))
    monkeypatch.setattr(ytstream, "ask_yes_no", lambda *a, **k: True)
    texts = iter(["/opt/vlc/vlc", "--aout=alsa"])
    monkeypatch.setattr(ytstream, "prompt", lambda *a, **k: next(texts))
    ytstream.settings_menu(cfg)
    assert cfg["stream_quality"] == "480" and cfg["stream_fullscreen"] is True and cfg["stream_resume"] is True
    assert cfg["stream_player"] == "/opt/vlc/vlc" and cfg["stream_args"] == "--aout=alsa"
    assert stream.load_history() == [] and len(saved) == 6


def test_option_dispatch(store, monkeypatch):
    seen = []
    monkeypatch.setattr(ytstream, "prompt", lambda *a, **k: "lofi")
    monkeypatch.setattr(ytstream, "search_and_play", lambda cfg, q: seen.append(("search", q)))
    monkeypatch.setattr(ytstream, "handle_text", lambda cfg, t: seen.append(("text", t)))
    monkeypatch.setattr(ytstream, "continue_watching", lambda cfg: seen.append("continue"))
    monkeypatch.setattr(ytstream, "history_menu", lambda cfg: seen.append("history"))
    monkeypatch.setattr(ytstream, "settings_menu", lambda cfg: seen.append("settings") or cfg)
    from ytgrab import channels
    monkeypatch.setattr(channels, "channels_menu", lambda cfg, **k: seen.append(("channels", callable(k["on_download"]))))
    for oid in ("search", "link", "continue", "history", "channels", "settings", "other"):
        ytstream.run_option({}, oid)
    assert seen == [("search", "lofi"), ("text", "lofi"), "continue", "history", ("channels", True), "settings"]


def test_the_dashboard_uses_the_stream_options(monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    st = home.HomeState(ytstream.OPTIONS, {"continue": "2 to resume"},
                        [{"title": "Kamata", "meta": "5:00 / 10:00"}], ytstream.HINTS, "Continue watching")
    text = plain("\n".join(home.render_home(st, 120, 40, 1.0)[0]))
    for label in ("Search & play", "Play a link", "Continue watching", "History", "My channels", "Settings"):
        assert label in text
    assert "2 to resume" in text and "Kamata" in text and "5:00 / 10:00" in text
    assert any(h in text for h in ytstream.HINTS)
    assert home.classify("3", ytstream.OPTIONS) == ("option", "continue")
    assert home.classify("https://youtu.be/x", ytstream.OPTIONS)[0] == "link"


def test_the_loop(store, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(ytstream, "_dashboard", lambda cfg: next(choices))
    monkeypatch.setattr(ytstream, "run_option", lambda cfg, oid: seen.append(("opt", oid)) or cfg)
    monkeypatch.setattr(ytstream, "handle_text", lambda cfg, t: seen.append(("text", t)))
    choices = iter([("option", "history"), ("search", "kendrick"), ("link", "youtu.be/x"), ("quit", None)])
    assert ytstream.interactive_loop({}) == 0
    assert seen == [("opt", "history"), ("text", "kendrick"), ("text", "youtu.be/x")]
    from ytgrab.ui import Back
    choices = iter([("option", "link"), ("quit", None)])
    monkeypatch.setattr(ytstream, "run_option", lambda cfg, oid: (_ for _ in ()).throw(Back()))
    assert ytstream.interactive_loop({}) == 0
    assert "Cancelled" in capsys.readouterr().out


def test_plain_terminal_loop(store, monkeypatch):
    monkeypatch.setattr(home, "interactive", lambda: False)
    answers = iter(["lofi", "youtu.be/x", "q"])
    monkeypatch.setattr(ytstream, "prompt", lambda *a, **k: next(answers))
    seen = []
    monkeypatch.setattr(ytstream, "handle_text", lambda cfg, t: seen.append(t))
    assert ytstream.interactive_loop({}) == 0 and seen == ["lofi", "youtu.be/x"]


# ===================================================================== command line
def test_run_with_a_target_plays_without_the_dashboard(monkeypatch):
    seen = []
    monkeypatch.setattr(ytstream, "handle_text", lambda cfg, t: seen.append((t, cfg.get("stream_quality"), cfg.get("stream_fullscreen"),
                                                                              cfg.get("stream_resume"), cfg.get("stream_player"))))
    monkeypatch.setattr(ytstream, "interactive_loop", lambda cfg: pytest.fail("no dashboard expected"))
    base = {"stream_quality": "1080", "stream_resume": True}
    assert ytstream.run(base, ["lofi", "mix"], quality="480", fullscreen=True, no_resume=True, vlc="/x/vlc") == 0
    assert seen == [("lofi mix", "480", True, False, "/x/vlc")]
    assert base == {"stream_quality": "1080", "stream_resume": True}               # the saved settings were not touched


def test_run_without_a_target_opens_the_dashboard(monkeypatch):
    monkeypatch.setattr(ytstream, "interactive_loop", lambda cfg: 7)
    assert ytstream.run({}, []) == 7


def test_main_arguments(store, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(ytstream, "run", lambda cfg, target, quality, fullscreen, no_resume, vlc: seen.append((list(target), quality, fullscreen, no_resume, vlc)) or 0)
    assert ytstream.main(["-q", "720", "--fullscreen", "--no-resume", "--vlc", "/v", "https://youtu.be/x"]) == 0
    assert seen == [(["https://youtu.be/x"], "720", True, True, "/v")]
    assert ytstream.main(["--version"]) == 0 and "ytstream (ytgrab)" in capsys.readouterr().out


def test_ctrl_c_is_a_clean_exit(store, monkeypatch, capsys):
    monkeypatch.setattr(ytstream, "run", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert ytstream.main([]) == 130 and "Bye" in capsys.readouterr().out


def test_ytgrab_stream_subcommand(store, monkeypatch):
    seen = []
    monkeypatch.setattr(ytstream, "run", lambda cfg, target, quality=None, **k: seen.append((list(target), quality)) or 0)
    monkeypatch.setattr(sys, "argv", ["ytgrab", "stream", "-q", "480", "lofi", "radio"])
    with pytest.raises(SystemExit) as e:
        main.main()
    assert e.value.code == 0 and seen == [(["lofi", "radio"], "480")]


def test_the_ytgrab_dashboard_offers_streaming(store, monkeypatch):
    assert any(o["id"] == "stream" for o in home.OPTIONS) and home.classify("stream")[1] == "stream"
    seen = []
    monkeypatch.setattr(ytstream, "interactive_loop", lambda cfg: seen.append("loop"))
    main._run_option(config.load_config(), "stream")
    assert seen == ["loop"]


def test_the_ytstream_command_is_installed():
    text = Path(__file__).resolve().parent.parent.joinpath("pyproject.toml").read_text()
    assert 'ytstream = "ytgrab.ytstream:main"' in text and 'ytgrab = "ytgrab.main:main"' in text


def test_defaults():
    d = config.DEFAULTS
    assert d["stream_quality"] == "1080" and d["stream_resume"] is True and d["stream_fullscreen"] is False
    assert d["stream_player"] == "" and d["stream_args"] == ""


def test_continue_rows_use_a_play_mark_not_a_tick(store, monkeypatch):
    monkeypatch.setattr(theme, "_COLOR", True)
    stream.record(info(), position=754, duration=2710)
    rows = ytstream.recent_entries(stream.continue_list())
    assert rows == [{"title": "Kamata (Official Video)", "mark": "▶", "meta": "12:34 / 45:10"}]
    st = home.HomeState(ytstream.OPTIONS, {}, rows, ytstream.HINTS, "Continue watching")
    text = plain("\n".join(home.render_home(st, 120, 40, 0)[0]))
    assert "▶ Kamata (Official Video)" in text and "✓" not in text
    plain_rows = home.HomeState(recent=[{"title": "T", "date": "2026-10-17 10:00"}])
    assert "✓ T" in plain("\n".join(home.render_home(plain_rows, 120, 40, 0)[0]))     # the download list keeps its tick


def test_stream_banner_has_no_download_folder_line(monkeypatch, capsys):
    monkeypatch.setattr(theme, "_COLOR", True)
    cfg = {"download_dir": "/home/x/Downloads", "banner_size": "large"}
    theme.banner(cfg, clear=False, word="STREAM", tagline=ytstream.TAGLINE, saving=False)
    out = plain(capsys.readouterr().out)
    assert "nothing is downloaded" in out and "saving to" not in out
    theme.banner(cfg, clear=False)
    assert "saving to" in plain(capsys.readouterr().out)
