import io
import subprocess
import sys
import zipfile

import pytest

from ytgrab import selfupdate as su


def make_project(root, version="1.2.3"):
    (root / "ytgrab").mkdir(parents=True)
    (root / "ytgrab" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (root / "pyproject.toml").write_text('[project]\nname = "ytgrab"\nversion = "%s"\n' % version)
    return root


def test_find_root_and_version(tmp_path):
    proj = make_project(tmp_path / "a" / "ytgrab")
    assert su.find_root(proj) == proj
    assert su.find_root(tmp_path / "a") == proj              # one level down
    assert su.find_root(tmp_path) == proj                    # two levels down
    assert su.read_version(proj) == "1.2.3"
    other = tmp_path / "b"; other.mkdir()
    (other / "pyproject.toml").write_text('[project]\nname = "something-else"\n')
    assert su.find_root(other) is None                       # a different project is ignored


@pytest.mark.parametrize("a,b", [("1.4.0", "1.3.9"), ("1.10.0", "1.9.9"), ("2.0", "1.99.99"), ("1.3.9-dev", "1.3.8")])
def test_version_ordering(a, b):
    assert su.version_tuple(a) > su.version_tuple(b)


def test_unpack_zip_keeps_few_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "SRC_DIR", tmp_path / "src")
    for v in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"):
        proj = make_project(tmp_path / f"build{v}" / "ytgrab", v)
        (proj / "ytgrab" / "__pycache__").mkdir()
        (proj / "ytgrab" / "__pycache__" / "x.pyc").write_text("junk")
        z = tmp_path / f"{v}.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for f in (tmp_path / f"build{v}").rglob("*"):
                zf.write(f, f.relative_to(tmp_path / f"build{v}"))
        dest = su.unpack_zip(z)
        assert dest.name == f"ytgrab-{v}" and su.read_version(dest) == v
        assert not (dest / "ytgrab" / "__pycache__").exists()          # junk not copied
    kept = sorted(p.name for p in (tmp_path / "src").iterdir())
    assert len(kept) == su.KEEP_UNPACKED and "ytgrab-1.4.0" in kept


def test_unpack_zip_rejects_bad_zips(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "SRC_DIR", tmp_path / "src")
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../../evil.txt", "x")
    with pytest.raises(ValueError, match="unsafe"):
        su.unpack_zip(evil)
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "hi")
    with pytest.raises(ValueError, match="does not contain"):
        su.unpack_zip(empty)
    assert not (tmp_path / "evil.txt").exists()


def test_install_command(monkeypatch, tmp_path):
    monkeypatch.setattr(su.shutil, "which", lambda n: "/usr/bin/pipx")
    cmd = su.install_command(tmp_path, use_pipx=True)
    assert cmd == ["/usr/bin/pipx", "install", "--force", f"{tmp_path}[tui]"]
    assert "-e" in su.install_command(tmp_path, editable=True, use_pipx=True)
    pip = su.install_command(tmp_path, use_pipx=False)
    assert pip[1:4] == ["-m", "pip", "install"] and pip[-1] == f"{tmp_path}[tui]"
    monkeypatch.setattr(su.shutil, "which", lambda n: None)            # no pipx on PATH
    assert su.install_command(tmp_path, use_pipx=True)[1:4] == ["-m", "pip", "install"]


def test_git_pull_cases(tmp_path):
    plain = tmp_path / "plain"; plain.mkdir()
    assert su.git_pull(plain) is None                                  # not a repo
    env = dict(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", PATH=__import__("os").environ["PATH"])
    def git(*a, cwd):
        return subprocess.run(["git", *a], cwd=cwd, env=env, check=True, capture_output=True, text=True)
    git("init", "-q", "--bare", "-b", "main", "remote.git", cwd=tmp_path)
    repo = make_project(tmp_path / "repo")
    git("init", "-q", "-b", "main", ".", cwd=repo)
    assert su.git_pull(repo) is None                                   # repo, no remote
    git("remote", "add", "origin", str(tmp_path / "remote.git"), cwd=repo)
    git("add", "-A", cwd=repo); git("commit", "-qm", "one", cwd=repo)
    assert su.git_pull(repo) is None                                   # remote but no upstream
    git("push", "-q", "-u", "origin", "main", cwd=repo)
    ok, text = su.git_pull(repo)
    assert ok                                                          # tracking branch: pull works


def test_self_update_errors_are_clean(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(su, "install_info", lambda: {"version": "1.0.0", "pipx": True,
                        "editable": False, "source": None, "prefix": "x"})
    assert su.self_update(None) == 2                                   # no source known
    assert su.self_update(str(tmp_path / "nope")) == 2
    assert su.self_update(str(tmp_path)) == 2                          # folder without a project
    assert "ytgrab self-update" in capsys.readouterr().out


def test_check_never_installs(tmp_path, monkeypatch, capsys):
    proj = make_project(tmp_path / "ytgrab", "9.9.9")
    monkeypatch.setattr(su, "install_info", lambda: {"version": "1.0.0", "pipx": True,
                        "editable": False, "source": proj, "prefix": "x"})
    monkeypatch.setattr(su.subprocess, "run", lambda *a, **k: pytest.fail("must not install"))
    assert su.self_update(None, check=True, no_git=True) == 0
    assert "update available" in capsys.readouterr().out


def test_preview_shows_release_notes_without_mutating_or_installing(tmp_path, monkeypatch, capsys):
    source = make_project(tmp_path / "source", "9.1.0")
    (source / "CHANGELOG.md").write_text("# Changelog\n\n## 9.1.0\n- Better dashboard\n- Safe rollback\n\n## 9.0.0\n- Old change\n")
    monkeypatch.setattr(su, "install_info", lambda: {"version": "9.0.0", "pipx": False,
                        "editable": False, "source": None, "prefix": "x"})
    monkeypatch.setattr(su, "_snapshot_current", lambda *a: pytest.fail("preview must not snapshot"))
    monkeypatch.setattr(su.subprocess, "run", lambda *a, **k: pytest.fail("preview must not install"))
    assert su.self_update(str(source), preview=True) == 0
    out = capsys.readouterr().out
    assert "update available" in out and "Better dashboard" in out
    assert "nothing was installed" in out


def test_snapshot_source_is_reinstallable_and_pruned(tmp_path, monkeypatch):
    source = make_project(tmp_path / "source", "1.2.3")
    (source / "ytgrab" / "main.py").write_text("# existing source")
    monkeypatch.setattr(su, "ROLLBACK_DIR", tmp_path / "rollback")
    snap = su._snapshot_current({"version": "1.2.3", "source": source, "editable": True})
    assert su.read_version(snap) == "1.2.3"
    assert (snap / "ytgrab" / "main.py").read_text() == "# existing source"


def test_rollback_installs_newest_snapshot(tmp_path, monkeypatch, capsys):
    from ytgrab.updater_ui import InstallResult
    source = make_project(tmp_path / "rollback" / "ytgrab-1.2.3-20260101", "1.2.3")
    monkeypatch.setattr(su, "ROLLBACK_DIR", source.parent)
    called = []
    monkeypatch.setattr(su, "install_command", lambda root: ["install", str(root)])
    monkeypatch.setattr(su, "run_installer", lambda cmd, **kw: (called.append((cmd, kw)) or InstallResult(0, [], 1.5)))
    assert su.rollback_install() == 0
    assert called and called[0][0][0] == "install" and "1.2.3" in called[0][1]["title"]
    assert "rolled back to v1.2.3" in capsys.readouterr().out


def test_failed_install_attempts_automatic_restore(tmp_path, monkeypatch, capsys):
    from ytgrab.updater_ui import InstallResult
    current = make_project(tmp_path / "current", "1.0.0")
    new = make_project(tmp_path / "new", "2.0.0")
    monkeypatch.setattr(su, "ROLLBACK_DIR", tmp_path / "rollback")
    monkeypatch.setattr(su, "install_info", lambda: {"version": "1.0.0", "pipx": False,
                        "editable": False, "source": current, "prefix": "x"})
    monkeypatch.setattr(su, "install_command", lambda root, **kw: ["install", str(root)])
    runs = []

    def fake(cmd, title="", **kw):
        runs.append(title)
        if "Restoring" in title:
            return InstallResult(0, [], 0.5)
        return InstallResult(9, ["Collecting x", "ERROR: no matching distribution"], 1.0)
    monkeypatch.setattr(su, "run_installer", fake)
    assert su.self_update(str(new), no_git=True) == 9
    out = capsys.readouterr().out
    assert "install failed (exit 9)" in out and "ERROR: no matching distribution" in out   # the installer's own words
    assert "restoring the previous version" in out
    assert "previous ytgrab v1.0.0 restored" in out
    assert runs == ["Installing ytgrab", "Restoring previous version"]
    assert list((tmp_path / "rollback").iterdir())


def test_snapshot_without_known_source_builds_reinstallable_project(tmp_path, monkeypatch):
    import tomllib
    monkeypatch.setattr(su, "ROLLBACK_DIR", tmp_path / "rollback")
    snapshot = su._snapshot_current({"version": "1.9.0", "source": None})
    metadata = tomllib.loads((snapshot / "pyproject.toml").read_text())
    assert metadata["project"]["name"] == "ytgrab"
    assert metadata["project"]["version"] == "1.9.0"
    assert su.find_root(snapshot) == snapshot


def test_preview_zip_shows_persistent_stage_without_writing(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path / "build" / "ytgrab", "3.0.0")
    (project / "CHANGELOG.md").write_text("## 3.0.0\n- New dashboard\n")
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for item in project.parent.rglob("*"):
            zf.write(item, item.relative_to(project.parent))
    stage = tmp_path / "persistent-source"
    monkeypatch.setattr(su, "SRC_DIR", stage)
    monkeypatch.setattr(su, "install_info", lambda: {"version": "2.0.0", "pipx": False,
                        "editable": False, "source": None, "prefix": "x"})
    assert su.self_update(str(archive), preview=True) == 0
    out = capsys.readouterr().out
    assert "stage plan" in out and str(stage / "ytgrab-3.0.0") in out
    assert not stage.exists()


def test_demo_animation_runs_real_panel_without_installing(monkeypatch, capsys):
    from ytgrab import updater_ui

    class Terminal(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr(sys, "stdout", Terminal())
    monkeypatch.setattr(su, "FakeInstaller", lambda delay=0.28: updater_ui.FakeInstaller(delay=0.01))
    monkeypatch.setattr(su, "install_command", lambda *a, **k: pytest.fail("demo must not install"))
    monkeypatch.setattr(su, "_snapshot_current", lambda *a, **k: pytest.fail("demo must not snapshot"))
    assert su.demo_update_animation() == 0
    out = sys.stdout.getvalue()
    assert "Animation preview complete" in out and "no update was installed" in out
    assert "\x1b[?25l" in out and "\x1b[?25h" in out            # cursor hidden while it runs, always restored


def test_demo_animation_is_safe_when_output_is_piped(monkeypatch, capsys):
    monkeypatch.setattr(su.sys.stdout, "isatty", lambda: False)
    assert su.demo_update_animation() == 0
    assert "needs an interactive terminal" in capsys.readouterr().out


def test_demo_mode_rejects_rollback_argument(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(su, "install_info", lambda: {"version": "1.10.0", "pipx": False,
                        "editable": False, "source": None, "prefix": "x"})
    monkeypatch.setattr(su, "rollback_install", lambda *a: pytest.fail("demo must not roll back"))
    assert su.self_update(rollback=True, demo_animation=True) == 2
    assert "standalone" in capsys.readouterr().out
