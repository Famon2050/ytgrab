"""Single-key input for the home dashboard and the channel explorer (Linux and macOS).

``open_keys()`` returns a reader whose ``read(timeout)`` gives one *token* per call, or None
when nothing was pressed. Tokens are plain strings: a printable character, or one of
UP DOWN LEFT RIGHT HOME END PGUP PGDN DELETE BACKSPACE ENTER TAB BTAB ESC and CTRL_A CTRL_D CTRL_E
CTRL_U CTRL_W. Pasted text arrives as a run of characters and is handed out one by one.
"""

import codecs
import os
import select
import sys
import collections
import time

if os.name == "nt":
    import msvcrt
else:
    msvcrt = None

try:
    import termios
    import tty
except ImportError:                     # not a POSIX terminal
    termios = tty = None

_CSI = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT", "H": "HOME", "F": "END", "Z": "BTAB"}
_TILDE = {"1": "HOME", "3": "DELETE", "4": "END", "5": "PGUP", "6": "PGDN", "7": "HOME", "8": "END"}
_CTRL = {"\x01": "CTRL_A", "\x04": "CTRL_D", "\x05": "CTRL_E", "\x15": "CTRL_U", "\x17": "CTRL_W",
         "\r": "ENTER", "\n": "ENTER", "\t": "TAB", "\x7f": "BACKSPACE", "\x08": "BACKSPACE", "\x03": "CTRL_C"}


def parse_keys(text):
    """Turn a chunk of decoded terminal input into tokens (pure function, easy to test)."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b":
            if i + 1 < n and text[i + 1] in "[O":
                j = i + 2
                while j < n and (text[j].isdigit() or text[j] in ";?"):
                    j += 1
                if j < n:
                    final, params = text[j], text[i + 2:j]
                    if final == "~":
                        tok = _TILDE.get(params.split(";")[0])
                    else:
                        tok = _CSI.get(final)
                    if tok:
                        out.append(tok)
                    i = j + 1
                    continue
                i = n                                   # truncated sequence: drop it
                continue
            out.append("ESC")
            i += 1
            continue
        if ch in _CTRL:
            out.append(_CTRL[ch])
        elif ch >= " ":
            out.append(ch)
        i += 1
    return out


class KeyReader:
    def __init__(self, fd=None):
        self.fd = sys.stdin.fileno() if fd is None else fd
        self._old = None if msvcrt is not None else termios.tcgetattr(self.fd)
        self._dec = codecs.getincrementaldecoder("utf-8")("replace")
        self._queue = collections.deque()
        if msvcrt is None:
            tty.setcbreak(self.fd)                     # keeps Ctrl+C working (ISIG stays on)

    def read(self, timeout=0.1):
        if self._queue:
            return self._queue.popleft()
        if msvcrt is not None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        special = msvcrt.getwch()
                        return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT",
                                "G": "HOME", "O": "END", "S": "DELETE"}.get(special)
                    return parse_keys(ch)[0] if parse_keys(ch) else None
                time.sleep(0.01)
            return None
        try:
            ready = select.select([self.fd], [], [], timeout)[0]
        except (OSError, ValueError):
            return None
        if not ready:
            return None
        try:
            data = os.read(self.fd, 4096)
        except OSError:
            return None
        if not data:
            return "CTRL_D"
        self._queue.extend(parse_keys(self._dec.decode(data)))
        return self._queue.popleft() if self._queue else None

    def close(self):
        if termios is not None and self._old is not None:
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self._old)
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def open_keys():
    """A KeyReader when stdin/stdout are a real terminal, else None."""
    try:
        if (termios is None and msvcrt is None) or not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
        return KeyReader()
    except Exception:
        return None
