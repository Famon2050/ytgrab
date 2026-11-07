"""Failure log: downloads that did not work, so `ytgrab retry` can try them again
(a cron run's failures otherwise just scroll away in a log file)."""

import json
import time

from . import config

MAX_ENTRIES = 500


def _path():
    return config.CONFIG_DIR / "failed.json"          # looked up at call time (tests redirect it)


def load():
    return config.read_json(_path(), [], list)


def _save(items):
    config.atomic_write_text(_path(), json.dumps(items[-MAX_ENTRIES:], indent=2, ensure_ascii=False))


def record(url, title="", error="", kind="", quality="", key=""):
    """Remember a failure (one entry per URL; repeated failures bump `attempts`)."""
    if not url:
        return
    now = time.strftime("%Y-%m-%d %H:%M")
    with config.file_lock(_path()):
        items = load()
        for it in items:
            if it.get("url") == url:
                it.update(error=error or it.get("error", ""), last_failed=now,
                          attempts=int(it.get("attempts", 1)) + 1)
                if title:
                    it["title"] = title
                if key:
                    it["key"] = key
                break
        else:
            items.append({"url": url, "title": title or url, "error": error, "kind": kind,
                          "quality": quality, "key": key, "first_failed": now,
                          "last_failed": now, "attempts": 1})
        _save(items)


def remove(urls):
    """Forget these URLs (they worked). Returns how many were removed."""
    urls = set(urls)
    if not urls:
        return 0
    with config.file_lock(_path()):
        items = load()
        keep = [it for it in items if it.get("url") not in urls]
        if len(keep) != len(items):
            _save(keep)
        return len(items) - len(keep)


def clear():
    with config.file_lock(_path()):
        n = len(load())
        _save([])
        return n


def count():
    return len(load())
