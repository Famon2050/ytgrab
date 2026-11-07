"""Pending downloads recorded by watch mode (renamed from queue.py so it
cannot shadow the standard-library ``queue`` module)."""

import json
import time

from .config import CONFIG_DIR, atomic_write_text, file_lock, read_json  # noqa: F401

QUEUE_FILE = CONFIG_DIR / "queue.json"


def _load():
    return read_json(QUEUE_FILE, [], list)


def _save(items):
    atomic_write_text(QUEUE_FILE, json.dumps(items, indent=2, ensure_ascii=False))


def add(url, title="", meta=None):
    """Add a URL. Returns True if added, False if already present."""
    with file_lock(QUEUE_FILE):
        items = _load()
        for it in items:
            if it.get("url") == url:
                return False
        items.append({
            "url": url,
            "title": title or url,
            "added": time.strftime("%Y-%m-%d %H:%M"),
            **(meta or {}),
        })
        _save(items)
        return True


def all_items():
    return _load()


def count():
    return len(_load())


def remove_by_url(url):
    return remove_urls([url]) > 0


def remove_urls(urls):
    """Remove several URLs in one locked update. Returns how many were removed."""
    urls = set(urls)
    with file_lock(QUEUE_FILE):
        items = _load()
        keep = [it for it in items if it.get("url") not in urls]
        if len(keep) == len(items):
            return 0
        _save(keep)
        return len(items) - len(keep)


def clear():
    with file_lock(QUEUE_FILE):
        _save([])
