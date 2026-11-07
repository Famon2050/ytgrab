from ytgrab import extras, keys


def test_termux_clipboard_detection_and_read(monkeypatch):
    monkeypatch.setenv("TERMUX_VERSION", "0.118")
    monkeypatch.setattr(extras.shutil, "which", lambda name: "/bin/termux-clipboard-get" if name == "termux-clipboard-get" else None)
    monkeypatch.setattr(extras.subprocess, "check_output", lambda *a, **kw: "https://youtu.be/abc\n")
    assert extras.clipboard_method() == "termux-clipboard-get"
    assert extras.clipboard_get() == "https://youtu.be/abc"


def test_windows_clipboard_provider_uses_powershell(monkeypatch):
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setattr(extras.os, "name", "nt")
    monkeypatch.setattr(extras.shutil, "which", lambda name: "/powershell.exe" if name == "powershell" else None)
    monkeypatch.setattr(extras.subprocess, "check_output", lambda *a, **kw: "copied text\n")
    assert extras.clipboard_method() == "powershell"
    assert extras.clipboard_get() == "copied text"


def test_windows_keyreader_uses_msvcrt(monkeypatch):
    class FakeMsvcrt:
        def __init__(self):
            self.values = iter(["h"])
        def kbhit(self):
            return True
        def getwch(self):
            return next(self.values)
    monkeypatch.setattr(keys, "msvcrt", FakeMsvcrt())
    reader = keys.KeyReader(fd=0)
    assert reader.read(timeout=0.01) == "h"
    reader.close()
