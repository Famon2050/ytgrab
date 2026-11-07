"""Test-suite wide setup. Runs before ytgrab is imported.

* ytgrab only colours a real terminal (so cron logs stay clean); tests capture
  output, so ask for colour explicitly.
* HOME points at a throw-away directory, so no test can ever read or write the
  developer's real ~/.config/ytgrab (history, cookies, subscriptions...).
"""
import atexit
import os
import shutil
import tempfile

os.environ.setdefault("YTGRAB_FORCE_COLOR", "1")
_home = tempfile.mkdtemp(prefix="ytgrab-test-home-")
os.environ["HOME"] = _home
os.environ.pop("XDG_DATA_HOME", None)
atexit.register(shutil.rmtree, _home, ignore_errors=True)
