"""Update ytgrab itself:  ytgrab self-update [folder | file.zip]

Three ways to get a new version of YOUR tool onto your machine:

  ytgrab self-update              re-install from the source folder it was installed from
                                  (after `git pull` when that folder is a git repo)
  ytgrab self-update ~/ytgrab     install from another folder
  ytgrab self-update new.zip      unpack a zip and install it

Your settings, history, archive and subscriptions live in ~/.config/ytgrab and are never touched.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import __version__
from .config import CONFIG_DIR
from .theme import C, paint
from .updater_ui import FakeInstaller, run_installer

if os.name == "nt":
    _DATA_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
else:
    _DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
DATA_DIR = _DATA_HOME / "ytgrab"
SRC_DIR = DATA_DIR / "src"            # where unpacked zips are kept
KEEP_UNPACKED = 3
ROLLBACK_DIR = DATA_DIR / "rollback"
KEEP_ROLLBACK = 3

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "build",
                                 "dist", "*.egg-info", ".git", ".venv")


def _ok(msg):   print(paint("  ✓  ", C.GREEN, C.BOLD) + msg)
def _err(msg):  print(paint("  ✕  ", C.RED, C.BOLD) + msg)
def _note(msg): print(paint("  ·  ", C.GREY_D) + paint(msg, C.GREY))


# ----------------------------------------------------------------------
# Where am I installed from?
# ----------------------------------------------------------------------
def _in_pipx():
    """True when this process runs from a pipx-managed venv (also with a custom PIPX_HOME)."""
    home = os.environ.get("PIPX_HOME")
    if home:
        try:
            if Path(sys.prefix).resolve().is_relative_to(Path(home).resolve()):
                return True
        except (OSError, ValueError, AttributeError):
            pass
    return "pipx" in Path(sys.prefix).parts


def install_info():
    """Describe the current installation (version, pipx?, editable?, source folder)."""
    info = {"version": __version__, "pipx": _in_pipx(),
            "editable": False, "source": None, "prefix": sys.prefix}
    try:
        raw = metadata.distribution("ytgrab").read_text("direct_url.json")
        du = json.loads(raw) if raw else {}
    except Exception:
        du = {}
    info["editable"] = bool((du.get("dir_info") or {}).get("editable"))
    url = du.get("url", "")
    if url.startswith("file://"):
        p = Path(unquote(urlparse(url).path))
        if p.exists():
            info["source"] = p
    return info


def find_root(path):
    """Folder that holds ytgrab's pyproject.toml (path itself, or up to 3 levels down)."""
    path = Path(path)
    candidates = [path] + [p.parent for p in sorted(path.glob("*/pyproject.toml"))] \
        + [p.parent for p in sorted(path.glob("*/*/pyproject.toml"))]
    for c in candidates:
        pp = c / "pyproject.toml"
        if pp.is_file() and re.search(r'^name\s*=\s*"ytgrab"', pp.read_text(encoding="utf-8"), re.M) \
                and (c / "ytgrab" / "__init__.py").is_file():
            return c
    return None


def read_version(root):
    try:
        text = (Path(root) / "ytgrab" / "__init__.py").read_text(encoding="utf-8")
        return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)
    except (OSError, AttributeError):
        return None


def version_tuple(v):
    """Sortable version: 1.4.1 > 1.4.0 > 1.4.0rc1 (a pre-release sorts below its release)."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)(.*)", v or "")
    if not m:
        return (0, 0, 0, 0, 0)
    nums = [int(x) for x in m.group(1).split(".")][:4]
    nums += [0] * (4 - len(nums))
    pre = bool(re.match(r"[-_.]?(a|b|c|rc|alpha|beta|pre|dev)", m.group(2).strip().lower()))
    return tuple(nums) + (0 if pre else 1,)


# ----------------------------------------------------------------------
# Getting new code
# ----------------------------------------------------------------------
def unpack_zip(zpath):
    """Unpack a ytgrab zip to ~/.local/share/ytgrab/src/ytgrab-<version>. Returns that folder."""
    with zipfile.ZipFile(zpath) as z:
        bad = [n for n in z.namelist() if n.startswith(("/", "\\")) or ".." in Path(n).parts]
        if bad:
            raise ValueError(f"unsafe path inside zip: {bad[0]}")
        tmp = Path(tempfile.mkdtemp(prefix="ytgrab-unzip-"))
        z.extractall(tmp)
    try:
        root = find_root(tmp)
        if root is None:
            raise ValueError("this zip does not contain a ytgrab project")
        dest = SRC_DIR / f"ytgrab-{read_version(root) or 'unknown'}"
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, dest, ignore=_IGNORE)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _prune_unpacked(keep=dest)
    return dest


def _prune_unpacked(keep):
    try:
        old = sorted((p for p in SRC_DIR.iterdir() if p.is_dir() and p != keep),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for p in old[max(KEEP_UNPACKED - 1, 0):]:
        shutil.rmtree(p, ignore_errors=True)


def _git(root, *args, timeout=120):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true")   # fail instead of prompting
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          env=env, timeout=timeout)


def git_pull(root):
    """git pull --ff-only if `root` is a git repo whose branch tracks a remote branch.
    Returns (ok, text), or None when there is nothing to pull from (not a repo / no remote / no upstream)."""
    root = Path(root)
    if not (root / ".git").exists() or not shutil.which("git"):
        return None
    try:
        remotes = _git(root, "remote")
        if remotes.returncode != 0 or not remotes.stdout.split():
            return None
        up = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if up.returncode != 0:                      # remote exists but no upstream branch is set
            return None
        r = _git(root, "pull", "--ff-only")
    except subprocess.TimeoutExpired:
        return False, "git timed out after 2 minutes (network down, or it wanted a password)"
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _snapshot_current(info):
    """Create a reinstallable copy of the installed source/package before replacing it."""
    ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    version = re.sub(r"[^A-Za-z0-9._-]", "_", str(info.get("version") or "unknown"))
    dest = ROLLBACK_DIR / f"ytgrab-{version}-{stamp}-{os.getpid()}"
    source = info.get("source")
    # Non-editable installs can have a source checkout that has since changed; copy
    # the imported package itself so the rollback really matches the running version.
    root = find_root(source) if source and info.get("editable") else None
    if root:
        shutil.copytree(root, dest, ignore=_IGNORE)
    else:
        # Non-editable installs may not expose their build source. Snapshot the exact
        # installed package into a minimal, reinstallable local project instead.
        dest.mkdir(parents=True)
        package = Path(__file__).resolve().parent
        shutil.copytree(package, dest / "ytgrab", ignore=_IGNORE)
        (dest / "pyproject.toml").write_text(
            f'''[build-system]
requires = [\"setuptools>=61.0\"]
build-backend = \"setuptools.build_meta\"

[project]
name = \"ytgrab\"
version = \"{version}\"
requires-python = \">=3.9\"
dependencies = [\"yt-dlp[default]>=2025.1.1\"]

[project.optional-dependencies]
tui = [\"textual>=8.0\"]

[project.scripts]
ytgrab = \"ytgrab.main:main\"
''', encoding="utf-8")
    backups = sorted((p for p in ROLLBACK_DIR.iterdir() if p.is_dir()),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[KEEP_ROLLBACK:]:
        shutil.rmtree(old, ignore_errors=True)
    return dest


def _preview_source(source, info):
    """Return source root and optional temporary directory without persistent writes."""
    if source:
        path = Path(source).expanduser()
    else:
        path = info.get("source")
        if path is None:
            return None, None
        path = Path(path)
    if path.is_dir():
        return find_root(path), None
    if path.is_file() and path.suffix.lower() == ".zip":
        temp = tempfile.TemporaryDirectory(prefix="ytgrab-preview-")
        try:
            with zipfile.ZipFile(path) as z:
                bad = [n for n in z.namelist() if n.startswith(("/", "\\")) or ".." in Path(n).parts]
                if bad:
                    raise ValueError(f"unsafe path inside zip: {bad[0]}")
                z.extractall(temp.name)
            root = find_root(temp.name)
            if root is None:
                raise ValueError("this zip does not contain a ytgrab project")
            return root, temp
        except Exception:
            temp.cleanup()
            raise
    return None, None


def _release_notes(root, version):
    try:
        text = (Path(root) / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return []
    lines, found = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if found: break
            found = version in line
        elif found and line.strip():
            lines.append(line.strip())
        if len(lines) >= 8: break
    return lines


def rollback_install(target=None):
    """Reinstall the newest saved version; never delete the snapshot after use."""
    backups = sorted((p for p in ROLLBACK_DIR.glob("ytgrab-*") if p.is_dir()),
                     key=lambda p: p.stat().st_mtime, reverse=True) if ROLLBACK_DIR.exists() else []
    if not backups:
        _err("no rollback snapshot found"); return 2
    root = Path(target).expanduser() if target else backups[0]
    root = find_root(root)
    if root is None:
        _err("rollback snapshot is incomplete"); return 2
    version = read_version(root) or "?"
    _note(f"restoring v{version} from {root}")
    cmd = install_command(root)
    res = run_installer(cmd, title=f"Restoring v{version}", style=_style())
    if not res.ok:
        _err("rollback install failed")
        _failure_box(res.tail)
        return res.rc or 1
    _ok(f"rolled back to v{version} in {res.seconds:.1f}s; restart ytgrab")
    return 0


def install_command(root, editable=False, use_pipx=None):
    """The command that installs `root` (with the TUI extra) in place of the current install."""
    # Must be an absolute path: pip reads a bare "ytgrab[tui]" as a PyPI package name.
    spec = f"{Path(root).expanduser().resolve()}[tui]"
    if use_pipx is None:
        use_pipx = _in_pipx()
    pipx = shutil.which("pipx")
    if use_pipx and pipx:
        return [pipx, "install", "--force"] + (["-e"] if editable else []) + [spec]
    return [sys.executable, "-m", "pip", "install", "--upgrade"] + (["-e"] if editable else []) + [spec]


# ----------------------------------------------------------------------
# The command
# ----------------------------------------------------------------------

def _style():
    try:
        from .config import load_config
        return load_config().get("bar_style", "classic")
    except Exception:
        return "classic"


def _tidy(path):
    p = str(path)
    home = str(Path.home())
    return "~" + p[len(home):] if p.startswith(home) else p


def _failure_box(tail, lines=12):
    """What the installer said last, shown only when an install fails."""
    import shutil as _sh
    cols = _sh.get_terminal_size((80, 24)).columns
    shown = [t for t in tail if t][-lines:]
    if not shown:
        return
    print(paint("  ╭─ installer output ─" + "─" * max(0, min(cols - 26, 50)), C.BORDER))
    for t in shown:
        text = t if len(t) <= cols - 8 else t[:cols - 9] + "…"
        print(paint("  │ ", C.BORDER) + paint(text, C.GREY))
    print(paint("  ╰" + "─" * max(0, min(cols - 6, 62)), C.BORDER))


def demo_update_animation():
    """Show the real updater panel against a scripted fake installer; never install anything."""
    interactive = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if not interactive:
        _note("Animation preview needs an interactive terminal; no update was installed.")
        return 0
    res = run_installer(["demo"], title="Installing ytgrab (demo)", style=_style(), interactive=True,
                        popen=lambda cmd, **kw: FakeInstaller(delay=0.28))
    if res.ok:
        print(paint("  ✓  Animation preview complete — no update was installed.", C.GREEN, C.BOLD))
        return 0
    print(paint("  ✕  Animation preview could not render; no update was installed.", C.RED, C.BOLD))
    return 1


def self_update(source=None, check=False, preview=False, rollback=False, demo_animation=False,
                no_git=False, reinstall=False, assume_yes=False):
    """Returns a process exit code."""
    info = install_info()
    print()
    print(paint("  ytgrab self-update", C.WHITE, C.BOLD))
    if demo_animation and (source or check or preview or rollback or reinstall or assume_yes):
        _err("--demo-animation is standalone; it never accepts an install source or update action")
        return 2
    if rollback:
        return rollback_install(source)
    if demo_animation:
        _note(f"installed: v{info['version']} (demo only; installation will not be changed)")
        return demo_update_animation()
    _note(f"installed: v{info['version']}"
          + (" (editable: your edits are already live)" if info["editable"] else "")
          + (f"   from {info['source']}" if info["source"] else ""))

    # Preview/check are strictly read-only: no git pull and no persistent ZIP extraction.
    if preview or check:
        temp = None
        try:
            root, temp = _preview_source(source, info)
            if root is None:
                _err("no ytgrab source found; give a folder or zip to preview")
                return 2
            new_v = read_version(root) or "?"
            _note(f"installed v{info['version']} → source v{new_v}")
            is_zip = bool(source and Path(source).suffix.lower() == ".zip")
            install_root = (SRC_DIR / f"ytgrab-{new_v}") if is_zip else Path(root).resolve()
            if is_zip:
                _note(f"stage plan: unpack ZIP into {install_root}")
            _note("install plan: " + " ".join(install_command(install_root,
                                                               use_pipx=info.get("pipx", False))))
            _note("safety: create a reinstallable snapshot of the current package before installing")
            if not source and not no_git:
                _note("Git pull is skipped in preview; a real update may fetch newer commits first.")
            notes = _release_notes(root, new_v)
            if notes:
                print(paint("  Changes in this release:", C.WHITE, C.BOLD))
                for line in notes:
                    print("    " + line)
            if version_tuple(new_v) > version_tuple(info["version"]):
                _ok(f"update available: v{info['version']} → v{new_v}")
            else:
                _ok("nothing newer in the source folder")
            if not check:
                _note("Preview only: nothing was installed, pulled, or changed.")
            return 0
        except (ValueError, zipfile.BadZipFile, OSError) as e:
            _err(f"preview failed: {e}"); return 2
        finally:
            if temp:
                temp.cleanup()

    # Preserve the installed source before the default update path can mutate it via git pull.
    backup = None
    if not source and info.get("source"):
        try:
            backup = _snapshot_current(info)
        except OSError as e:
            _err(f"could not create a safety snapshot; update cancelled: {e}")
            return 1

    # ---- find the new code ----
    from_zip = False
    if source:
        p = Path(source).expanduser()
        if not p.exists():
            _err(f"not found: {p}"); return 2
        try:
            if p.is_file():
                if p.suffix.lower() != ".zip":
                    _err("give a folder or a .zip file"); return 2
                root, from_zip = unpack_zip(p), True
            else:
                root = find_root(p)
        except (ValueError, zipfile.BadZipFile) as e:
            _err(str(e)); return 2
        if root is None:
            _err(f"no ytgrab project found in {p}"); return 2
    else:
        root = info["source"] and find_root(info["source"])
        if root is None:
            _err("I don't know where your source folder is.")
            _note("Tell me:   ytgrab self-update ~/path/to/ytgrab   or   ytgrab self-update new.zip")
            return 2
        if not no_git:
            pulled = git_pull(root)
            if pulled is None:
                _note("no git upstream to pull from, installing the files as they are")
            elif pulled[0]:
                _ok("git pull: " + (pulled[1].splitlines()[-1] if pulled[1] else "done"))
            else:
                _err("git pull failed (resolve it in the repo, or use --no-git):")
                print(paint("     " + pulled[1].replace("\n", "\n     "), C.GREY)); return 1

    new_v = read_version(root) or "?"
    _note(f"source:    v{new_v}   {root}")
    if version_tuple(new_v) < version_tuple(info["version"]):
        _note(f"v{new_v} is OLDER than the installed v{info['version']}")
        if not check and not assume_yes:
            try:
                answer = input("     Install the older version anyway? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer != "y":
                print("  Cancelled."); return 1
    editable = info["editable"] and not from_zip
    if editable and not reinstall and info["source"] and Path(root).resolve() == Path(info["source"]).resolve():
        _ok("editable install: the code you edit is the code that runs. Nothing to do.")
        _note("(use --reinstall to refresh dependencies)")
        return 0

    if backup is None:
        try:
            backup = _snapshot_current(info)
        except OSError as e:
            _err(f"could not create a safety snapshot; update cancelled: {e}")
            return 1
    _ok(f"rollback snapshot ready: {backup}")
    cmd = install_command(root, editable=editable)
    _note("running: " + " ".join(cmd))
    print()
    try:
        res = run_installer(cmd, title="Installing ytgrab", style=_style())
    except OSError as e:
        _err(f"could not start installer: {e}")
        return 1
    print()
    if not res.ok:
        _err("update aborted by you" if res.aborted else f"install failed (exit {res.rc})")
        _failure_box(res.tail)
        _note("restoring the previous version…")
        back = run_installer(install_command(backup), title="Restoring previous version", style=_style())
        if back.ok:
            _ok(f"previous ytgrab v{info['version']} restored from {_tidy(backup)}")
        else:
            _err("automatic rollback also failed; the snapshot is intact:")
            _failure_box(back.tail)
            _note(f"snapshot: {backup}   (retry:  ytgrab self-update --rollback)")
        return 130 if res.aborted else (res.rc or 1)
    _ok(f"installed in {res.seconds:.1f}s")
    # verify with the freshly installed command, not this (old) process
    exe = shutil.which("ytgrab")
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip() if exe else ""
    _ok(f"updated → {out or 'v' + new_v}")
    _note("restart ytgrab to use it. Your settings/history were not touched.")
    return 0


def about():
    info = install_info()
    try:
        import yt_dlp
        ytv = yt_dlp.version.__version__
    except Exception:
        ytv = "?"
    try:
        import textual
        tv = textual.__version__
    except Exception:
        tv = "not installed  (pipx inject ytgrab textual)"
    kind = ("pipx, editable" if info["editable"] and info["pipx"] else "pipx" if info["pipx"]
            else "editable" if info["editable"] else "pip")
    rows = [("ytgrab", f"v{info['version']}"), ("install", kind),
            ("source", str(info["source"] or "unknown")), ("yt-dlp", ytv), ("textual", tv),
            ("python", sys.version.split()[0]), ("config", _tidy(CONFIG_DIR))]
    print()
    for k, v in rows:
        print(paint(f"  {k:<9}", C.GREY_D) + paint(v, C.OFF))
    print()
