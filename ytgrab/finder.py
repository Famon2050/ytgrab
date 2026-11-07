"""`ytgrab find`: search everything you have downloaded, then open it.

Looks at your history (title, channel, link, file name) and also at the files in
your download folder, so videos that are not in the history (moved, copied in,
downloaded by an older version) are found too.
"""

import os
import sys
import time
import unicodedata
from pathlib import Path

from . import config as _config
from .fmt import fmt_size
from .library import iter_media
from .theme import C, paint
from .ui import prompt
from .ytdl import open_path


def _fold(s):
    """Lower-case, accents removed: "Beyoncé" matches "beyonce"."""
    s = unicodedata.normalize("NFKD", str(s or "")).casefold()
    return "".join(ch for ch in s if not unicodedata.combining(ch))


_AUDIO = {".mp3", ".m4a", ".opus", ".wav", ".flac", ".ogg", ".aac", ".wma"}


class Hit:
    __slots__ = ("title", "channel", "kind", "size", "date", "file", "url", "exists", "score", "ts")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k, "" if k not in ("size", "score", "ts", "exists") else 0))


def _tokens(query):
    q = query if isinstance(query, str) else " ".join(query)
    return [t for t in _fold(q).split() if t]


def _score(tokens, title, channel, rest):
    """None unless EVERY word matches somewhere; higher = better match."""
    score = 0
    for t in tokens:
        if t in title:
            score += 3 + (1 if title.startswith(t) or f" {t}" in title else 0)
        elif t in channel:
            score += 2
        elif t in rest:
            score += 1
        else:
            return None
    phrase = " ".join(tokens)
    if len(tokens) > 1 and phrase in title:
        score += 4
    return score


def search(query, download_dir=None, history=None, kind=None, limit=20):
    """-> list of Hit, best first (ties: newest first)."""
    tokens = _tokens(query)
    if not tokens:
        return []
    hist = _config.load_history() if history is None else history
    hits, known = [], set()
    for h in hist:
        if not isinstance(h, dict):
            continue
        f = str(h.get("file") or "")
        title, channel = _fold(h.get("title")), _fold(h.get("channel"))
        rest = _fold(f"{os.path.basename(f)} {h.get('url', '')} {h.get('quality', '')}")
        sc = _score(tokens, title, channel, rest)
        if f:
            known.add(os.path.realpath(f))
        if sc is None:
            continue
        k = h.get("kind") or ("audio" if Path(f).suffix.lower() in _AUDIO else "video")
        hits.append(Hit(title=h.get("title") or os.path.basename(f), channel=h.get("channel", ""),
                        kind=k, size=h.get("size") or 0, date=h.get("date", ""), file=f,
                        url=h.get("url", ""), exists=bool(f) and os.path.isfile(f), score=sc,
                        ts=_ts(h.get("date"))))
    if download_dir and Path(download_dir).is_dir():
        for p, size, mtime in iter_media(download_dir):
            if os.path.realpath(p) in known:
                continue
            stem = p.stem
            sc = _score(tokens, _fold(stem), "", _fold(str(p.parent)))
            if sc is None:
                continue
            hits.append(Hit(title=stem, channel="", kind="audio" if p.suffix.lower() in _AUDIO else "video",
                            size=size, date=time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)),
                            file=str(p), exists=True, score=sc, ts=mtime))
    if kind:
        hits = [h for h in hits if h.kind == kind]
    hits.sort(key=lambda h: (-h.score, -h.ts))
    return hits[:limit] if limit else hits


def _ts(date):
    try:
        return time.mktime(time.strptime(date, "%Y-%m-%d %H:%M"))
    except (TypeError, ValueError):
        return 0


def _short(path, n=70):
    path = str(path).replace(str(Path.home()), "~")
    return path if len(path) <= n else "…" + path[-(n - 1):]


def _print_hits(hits):
    for n, h in enumerate(hits, 1):
        bits = [b for b in (h.kind, fmt_size(h.size) if h.size else "", h.date) if b]
        print(paint(f"  {n:>2}  ", C.RED, C.BOLD) + paint(h.title[:60], C.WHITE, C.BOLD)
              + (paint(f"  —  {h.channel[:28]}", C.GREY) if h.channel else ""))
        if h.exists:
            print(paint(f"      {_short(h.file)}", C.GREY_D) + paint("   " + "  ·  ".join(bits), C.GREY_D))
        else:
            where = f"   {_short(h.file)}" if h.file else ""
            print(paint("      ✗ file not found" + where, C.AMBER) + paint("   " + "  ·  ".join(bits), C.GREY_D))


def find_command(cfg, words, kind=None, limit=20, interactive=None):
    """Search and (on a terminal) offer to open results. Returns 0 if anything was found."""
    tokens = _tokens(words)
    if not tokens:
        print(paint("  Usage: ytgrab find WORDS…      e.g.  ytgrab find kendrick lamar", C.RED))
        return 2
    hits = search(words, cfg.get("download_dir"), kind=kind, limit=limit)
    print()
    if not hits:
        print(paint(f"  Nothing found for “{' '.join(tokens)}”.", C.AMBER))
        print()
        return 1
    print(paint(f"  {len(hits)} result(s) for “{' '.join(tokens)}”", C.WHITE, C.BOLD))
    print()
    _print_hits(hits)
    print()
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    while interactive:
        raw = prompt("  open: number   folder: f+number   Enter=done  › ").lower().strip()
        if not raw:
            break
        want_folder = raw.startswith("f")
        num = raw[1:] if raw[:1] in ("f", "o") else raw
        if not num.strip().isdigit() or not 1 <= int(num) <= len(hits):
            print(paint(f"     Type 1-{len(hits)}, or f1 for the folder.", C.AMBER))
            continue
        h = hits[int(num) - 1]
        target = Path(h.file) if h.file else None
        if target is None or not h.exists:
            print(paint("     That file is no longer there.", C.AMBER))
            continue
        open_path(target.parent if want_folder else target)
    return 0
