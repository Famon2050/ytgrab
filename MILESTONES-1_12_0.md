# ytgrab 1.12.0 — Milestone 2 of 3: the home dashboard and My channels

| Milestone | Completion criteria | Status |
|---|---|---|
| 1. Dashboard | Banner, input box with a rotating placeholder, option list with arrow-key and typed selection, badges, recent downloads, adapts to any window size, plain-terminal fallback, empty Enter no longer quits. | **Complete** |
| 2. My channels | Save by @handle / link / name search, local only, never auto-downloaded; explorer with Home / Videos / Shorts / Live / Playlists / Podcasts, playlist drill-down, tick-and-download, NEW and downloaded markers, new-upload check. | **Complete** |
| 3. Verify and package | Unit tests for every pure part, scripted-terminal tests for the loops, real pseudo-terminal runs at several sizes, full suite green. | **Complete** |

## Try it

* `ytgrab` — the dashboard. Press ↓ ↓ Enter, or type `3` Enter.
* `ytgrab channels` — straight to My channels. `a` to add (try `@diamondplatnumz`, or just `lofi girl`).
* In a channel: ← → tabs, Space to tick, Enter to download, Esc to go back.

## Design notes

* `keys.py` reads single keys (arrows, paste, Ctrl codes) and `parse_keys` is a pure function.
* `home.py` keeps all drawing in `render_home`, a pure function that returns the lines and the cursor position;
  `run_home` is the thin loop around it. `LineEditor` and `classify` are pure too.
* `channels.py` separates data (`fetch_tab`, `add_channel`, ...), state (`Explorer`, `ChannelList`), drawing
  (`render_*`) and the loops. Network work runs in background threads so the screen keeps animating.
* Saved channels reuse the existing `subs` store, adding `auto: false`; `subs.sync` skips those records.
* "New" means: not among the videos you saw the last time you opened that channel (ids are remembered in the
  record). Flat extraction does not give upload dates, so this is deliberately about *your* visits.

## Honest limits

* The channel tabs call yt-dlp's channel extraction. I could not reach YouTube from where this was built, so the
  tab URLs (`/featured`, `/videos`, `/shorts`, `/streams`, `/playlists`, `/podcasts`) and the channel-name search
  were tested against scripted responses, not live. Please try one real channel; if a tab misbehaves, the error is
  shown on that tab instead of crashing.
* Podcasts and Home depend on what each channel has; missing tabs show "Nothing here".
* Linux and macOS terminals only (Windows is intentionally not part of this work).

## Verification

* Full regression suite: **658 passed** (100 new for this milestone).
* The dashboard and every channel screen were driven in a real pseudo-terminal (150x46, 80x24, 60x30) and
  inspected as images.
