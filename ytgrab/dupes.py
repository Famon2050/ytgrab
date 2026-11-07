"""`ytgrab dupes`: find duplicate files in your download folder and clean them up.

Three kinds of duplicates are found, strongest first:
  identical    byte-for-byte copies (same size and same content hash)
  same video   ytgrab downloaded the same video twice (e.g. 720p and later 1080p)
  same title   the same name once numbering/"(1)"/"copy" is ignored (it may be a different video)

Nothing is ever deleted behind your back: files go to the system trash, only
verified-identical copies are cleaned automatically, and everything else is
your decision.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.parse
from collections import defaultdict
from pathlib import Path

from . import config as _config
from .fmt import fmt_size
from .library import _key_from_url, iter_media
from .theme import C, paint
from .ui import Spinner, ask_yes_no, prompt

_AUDIO = {".mp3", ".m4a", ".opus", ".wav", ".flac", ".ogg", ".aac", ".wma"}
_CHUNK = 1 << 20


# ---------------------------------------------------------------- hashing
def _cls(path):
    return "audio" if Path(path).suffix.lower() in _AUDIO else "video"


def partial_hash(path, size):
    """Fast fingerprint: size + first/middle/last MiB. Equal fingerprints are
    *very* likely identical; deletion still re-checks with `full_hash`."""
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        if size <= 3 * _CHUNK:
            h.update(f.read())
        else:
            for off in (0, size // 2 - _CHUNK // 2, size - _CHUNK):
                f.seek(off)
                h.update(f.read(_CHUNK))
    return h.hexdigest()


def full_hash(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def norm_title(stem):
    """File name without playlist numbers, [video id], "(1)" and "copy" noise."""
    s = unicodedata.normalize("NFKC", stem).casefold()
    s = re.sub(r"^\d{1,4} - ", "", s)
    s = re.sub(r"\s*\[[\w-]{11}\]\s*$", "", s)
    s = re.sub(r"(?:\s*[-_ ]*\(\d{1,2}\)|\s*-?\s*copy(?: \(\d+\))?)\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- grouping
class Item:
    __slots__ = ("path", "size", "mtime", "phash", "in_history")

    def __init__(self, path, size, mtime):
        self.path, self.size, self.mtime = Path(path), size, mtime
        self.phash, self.in_history = None, False


class Group:
    def __init__(self, items, reason):
        self.items, self.reason = items, reason
        self.keep = self._suggest()

    LABELS = {"identical": "identical copies", "same video": "same video", "same title": "same title"}

    def _suggest(self):
        """Index of the file to keep: biggest (best quality), then the one the
        history points at, then the oldest, then the shortest name."""
        ranked = sorted(range(len(self.items)), key=lambda i: (
            -self.items[i].size, not self.items[i].in_history,
            self.items[i].mtime, len(str(self.items[i].path))))
        return ranked[0]

    @property
    def name(self):
        return self.items[self.keep].path.stem

    @property
    def reclaimable(self):
        return sum(it.size for i, it in enumerate(self.items) if i != self.keep)

    @property
    def label(self):
        return self.LABELS[self.reason]


class _Union:
    def __init__(self):
        self.parent, self.kinds = {}, {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b, kind):
        ra, rb = self.find(a), self.find(b)
        kinds = self.kinds.get(ra, set()) | self.kinds.get(rb, set()) | {kind}
        self.parent[rb] = ra
        self.kinds[ra] = kinds


def _history_files():
    """{real file path: archive key} for EVERY download in the history (not just
    the newest per video), so two files of the same video can be paired up."""
    out = {}
    for h in _config.load_history():
        if not isinstance(h, dict) or not h.get("file"):
            continue
        key = h.get("key") or _key_from_url(h.get("url"))
        if key:
            out[os.path.realpath(h["file"])] = key
    return out


def find_groups(root, progress=None):
    """-> (groups, files_checked). Biggest savings first."""
    items = [Item(p, sz, mt) for p, sz, mt in iter_media(root)]
    hist = _history_files()
    for it in items:
        it.in_history = os.path.realpath(it.path) in hist

    uf = _Union()
    for it in items:
        uf.find(id(it))

    by_size = defaultdict(list)
    for it in items:
        by_size[it.size].append(it)
    for same in by_size.values():
        if len(same) < 2:
            continue
        by_hash = defaultdict(list)
        for it in same:
            try:
                it.phash = partial_hash(it.path, it.size)
            except OSError:
                continue
            by_hash[it.phash].append(it)
        for twins in by_hash.values():
            for other in twins[1:]:
                uf.union(id(twins[0]), id(other), "hash")
        if progress:
            progress()

    by_key = defaultdict(list)
    for it in items:
        key = hist.get(os.path.realpath(it.path))
        if key:
            by_key[(key, _cls(it.path))].append(it)
    for same in by_key.values():
        for other in same[1:]:
            uf.union(id(same[0]), id(other), "key")

    # chapter files ("Video/001 - Intro.mp4" next to "Video.mp4") share names like
    # "Intro" across many videos; they are not duplicates of each other
    stems = {(it.path.parent, it.path.stem) for it in items}
    by_title = defaultdict(list)
    for it in items:
        if (it.path.parent.parent, it.path.parent.name) in stems:
            continue
        t = norm_title(it.path.stem)
        if len(t) >= 4:
            by_title[(t, _cls(it.path))].append(it)
    for same in by_title.values():
        for other in same[1:]:
            uf.union(id(same[0]), id(other), "title")

    members = defaultdict(list)
    for it in items:
        members[uf.find(id(it))].append(it)
    groups = []
    for root_id, mem in members.items():
        if len(mem) < 2:
            continue
        if len({m.size for m in mem}) == 1 and len({m.phash for m in mem}) == 1 and mem[0].phash:
            reason = "identical"
        elif "key" in uf.kinds.get(root_id, set()):
            reason = "same video"
        else:
            reason = "same title"
        groups.append(Group(sorted(mem, key=lambda m: str(m.path)), reason))
    order = {"identical": 0, "same video": 1, "same title": 2}
    groups.sort(key=lambda g: (order[g.reason], -g.reclaimable))
    return groups, len(items)


# ---------------------------------------------------------------- deleting
def move_to_trash(path):
    """Send a file to the system trash (never a hard delete). True on success."""
    path = Path(path)
    try:
        if sys.platform.startswith("darwin"):
            dest = Path.home() / ".Trash"
            dest.mkdir(exist_ok=True)
            return _unique_move(path, dest)
        if os.name == "nt":
            dest = _config.CONFIG_DIR / "trash"
            dest.mkdir(parents=True, exist_ok=True)
            return _unique_move(path, dest)
        gio = shutil.which("gio")
        if gio:
            r = subprocess.run([gio, "trash", str(path)], capture_output=True, timeout=60)
            if r.returncode == 0:
                return True
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Trash"
        (base / "files").mkdir(parents=True, exist_ok=True)
        (base / "info").mkdir(parents=True, exist_ok=True)
        name, n = path.name, 1
        while (base / "files" / name).exists() or (base / "info" / f"{name}.trashinfo").exists():
            n += 1
            name = f"{path.stem} ({n}){path.suffix}"
        info = (f"[Trash Info]\nPath={urllib.parse.quote(str(path.resolve()))}\n"
                f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        (base / "info" / f"{name}.trashinfo").write_text(info, encoding="utf-8")
        shutil.move(str(path), str(base / "files" / name))
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _unique_move(path, dest_dir):
    name, n = path.name, 1
    while (dest_dir / name).exists():
        n += 1
        name = f"{path.stem} ({n}){path.suffix}"
    shutil.move(str(path), str(dest_dir / name))
    return True


def retarget_history(moves):
    """History entries that pointed at a removed file now point at the kept one."""
    if not moves:
        return
    try:
        with _config.file_lock(_config.HISTORY_FILE):
            hist = _config.load_history()
            changed = False
            for h in hist:
                old = str(h.get("file") or "")
                try:
                    old = str(Path(old).resolve()) if old else old
                except OSError:
                    pass
                new = moves.get(old)
                if new:
                    h["file"] = new
                    changed = True
            if changed:
                _config.save_history(hist)
    except OSError:
        pass


def apply_plan(plan):
    """plan: [(Group, keep_index)]. Returns (files_removed, bytes_freed, problems).
    Identical groups are re-verified with a full hash before anything is removed."""
    removed, freed, problems, moves = 0, 0, [], {}
    for g, keep in plan:
        kept = g.items[keep]
        kept_hash = None
        for i, it in enumerate(g.items):
            if i == keep:
                continue
            try:
                if g.reason == "identical":
                    kept_hash = kept_hash or full_hash(kept.path)
                    if full_hash(it.path) != kept_hash:
                        problems.append(f"not identical after all, left alone: {it.path.name}")
                        continue
                if move_to_trash(it.path):
                    removed += 1
                    freed += it.size
                    moves[str(it.path.resolve())] = str(kept.path.resolve())
                else:
                    problems.append(f"could not trash: {it.path}")
            except OSError as e:
                problems.append(f"{it.path.name}: {e}")
    retarget_history(moves)
    return removed, freed, problems


# ---------------------------------------------------------------- the command
def _rel(path, root):
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)


def _date(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _summary_line(groups):
    free = sum(g.reclaimable for g in groups)
    files = sum(len(g.items) for g in groups)
    return (f"{len(groups)} group(s)  ·  {files} files  ·  "
            f"{fmt_size(free)} can be freed")


def _print_groups(groups, root, limit=None):
    shown = groups if limit is None else groups[:limit]
    for n, g in enumerate(shown, 1):
        color = C.GREEN if g.reason == "identical" else C.AMBER
        print(paint(f"  {n:>3}  ", C.RED, C.BOLD) + paint(f"{g.label:<18}", color, C.BOLD)
              + paint(f"{len(g.items)} files  ", C.GREY)
              + paint(f"frees {fmt_size(g.reclaimable):<9}", C.WHITE)
              + paint(g.name[:44], C.OFF))
    if limit is not None and len(groups) > limit:
        print(paint(f"       … and {len(groups) - limit} more", C.GREY_D))


def _show_group(g, n, total, root):
    print()
    print(paint(f"  Group {n}/{total}  ·  {g.label}  ·  frees {fmt_size(g.reclaimable)}", C.WHITE, C.BOLD))
    if g.reason == "same title":
        print(paint("  (matched by name only; these may be different videos)", C.GREY_D))
    for i, it in enumerate(g.items, 1):
        star = paint("★", C.AMBER, C.BOLD) if i - 1 == g.keep else " "
        hist = paint(" ✓history", C.GREY_D) if it.in_history else ""
        print(paint(f"   {i}  ", C.RED, C.BOLD) + star + " "
              + paint(_rel(it.path, root), C.OFF)
              + paint(f"   {fmt_size(it.size)}  ·  {_date(it.mtime)}", C.GREY) + hist)


def _review(groups, root):
    """Ask, group by group, which file to keep. Returns [(Group, keep_index)]."""
    plan = []
    for n, g in enumerate(groups, 1):
        _show_group(g, n, len(groups), root)
        while True:
            raw = prompt(f"  Keep which?  [1-{len(g.items)}]  Enter=keep ★  [s]kip  [q]uit review  › ").lower()
            if raw == "q":
                return plan
            if raw == "s":
                break
            if raw == "":
                plan.append((g, g.keep)); break
            if raw.isdigit() and 1 <= int(raw) <= len(g.items):
                plan.append((g, int(raw) - 1)); break
            print(paint("     Type a file number, Enter, s or q.", C.AMBER))
    return plan


def _finish(plan):
    removed, freed, problems = apply_plan(plan)
    print()
    print(paint(f"  ✓  Moved {removed} file(s) to the trash  ·  freed {fmt_size(freed)}", C.GREEN, C.BOLD))
    for p in problems:
        print(paint(f"  ⚠  {p}", C.AMBER))
    if removed:
        print(paint("     (restore anything from your trash if you change your mind)", C.GREY_D))
    return 0


def dupes_command(cfg, dry_run=False, auto=False, directory=None):
    """Returns an exit code (0 ok, 2 bad folder)."""
    root = Path(directory or cfg.get("download_dir", ".")).expanduser()
    if not root.is_dir():
        print(paint(f"  ✕  Not a folder: {root}", C.RED))
        return 2
    print()
    spin = Spinner(f"Scanning {root}")
    spin.start()
    try:
        groups, checked = find_groups(root)
    finally:
        spin.stop()
    if not groups:
        print(paint(f"  ✓  No duplicates found  ({checked} files checked in {root})", C.GREEN, C.BOLD))
        print()
        return 0
    print(paint(f"  Found duplicates in {root}", C.WHITE, C.BOLD))
    print(paint("  " + _summary_line(groups), C.GREY))
    print()
    _print_groups(groups, root, limit=None if (dry_run or not sys.stdout.isatty()) else 15)
    print()
    ident = [g for g in groups if g.reason == "identical"]

    if dry_run:
        print(paint("  Dry run: nothing was changed.", C.GREY_D))
        return 0
    if auto:
        if not ident:
            print(paint("  Nothing to clean automatically (only verified identical copies are).", C.GREY))
            return 0
        return _finish([(g, g.keep) for g in ident])
    if not sys.stdin.isatty():
        return 0

    while True:
        opts = []
        if ident:
            opts.append(f"[a]uto-clean {len(ident)} identical group(s) ({fmt_size(sum(g.reclaimable for g in ident))})")
        opts += ["[r]eview each group", "[q]uit"]
        raw = prompt("  " + "   ".join(opts) + "  › ").lower()
        if raw == "a" and ident:
            if ask_yes_no(f"Move {sum(len(g.items) - 1 for g in ident)} duplicate file(s) to the trash?", default=True):
                return _finish([(g, g.keep) for g in ident])
        elif raw == "r":
            plan = _review(groups, root)
            if not plan:
                print(paint("  Nothing selected.", C.GREY))
                return 0
            n = sum(len(g.items) - 1 for g, _ in plan)
            free = sum(sum(it.size for i, it in enumerate(g.items) if i != k) for g, k in plan)
            if ask_yes_no(f"Move {n} file(s) ({fmt_size(free)}) to the trash?", default=False):
                return _finish(plan)
            print(paint("  Nothing was deleted.", C.GREY))
            return 0
        elif raw in ("q", ""):
            return 0
