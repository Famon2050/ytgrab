# Changelog

## 1.14.1
### Bug fixes
- Streaming quality caps now use the shorter video dimension, including portrait Shorts, and reject formats above the selected cap instead of falling back to uncapped quality.
- Same-title duplicate detection and recorded-file recovery now accept only recognized audio/video extensions, avoiding subtitle/document false positives.
- Added regression tests for portrait streams, strict cap enforcement, subtitles and non-media history siblings.

## 1.14.0
### Cross-platform: Windows 10/11 and Android Termux
- Added PowerShell installation for Windows, with a dedicated virtual environment and user-PATH setup.
- Added a Termux installer using `pkg`, with Python, FFmpeg and Node.js inside the Termux environment.
- Added Windows terminal key input for the home dashboard and download controls, and Windows cross-process file locks.
- Added PowerShell clipboard reads, Termux clipboard/open/notification integrations, Windows VLC detection and native app-data locations.
- Documented platform requirements and the Termux shared-storage setup; stock Termux's desktop-VLC streaming limitation is explicit.

## 1.13.0
### New: `ytstream`, watch YouTube in VLC without downloading
- Type **`ytstream`** (or `ytgrab stream`, or option 4 "Stream in VLC" on the home screen) to open the streaming
  mode: its own dashboard with a big STREAM banner, an input box, and the options **Search & play**, **Play a link**,
  **Continue watching**, **History**, **My channels** and **Settings**.
- Search or paste a link, pick a video and **VLC opens ready to stream**: ytgrab asks yt-dlp for the direct stream
  (video and audio together, or as two streams) and hands it to VLC. Nothing is written to your storage.
  `ytstream https://youtu.be/...` plays a link at once; `ytstream lofi hip hop` searches. `1,3,5` queues several.
- **It remembers your place.** While VLC plays, ytgrab reads its position through VLC's remote-control interface.
  When VLC is closed, the last minute is saved; the next time you pick that video you are offered
  *Resume from 12:34 of 45:10 · start over · cancel*, and **Continue watching** lists everything unfinished.
  Videos you watch to the end are marked watched.
- A small **now-playing panel** shows title, channel, quality, a live progress bar with the time and percentage, and
  lets you control VLC from the terminal: space pauses, ← → seek 10 s, `q` stops.
- **History** keeps up to 300 videos with your place in each (play, delete one, clear all). **My channels** works
  here too: explore a saved creator and press Enter to *watch* instead of download.
- **Settings**: quality cap (best / 1080 / 720 / 480 / 360), start fullscreen, offer to resume, path to VLC, extra VLC
  options. `ytstream -q 720 --fullscreen --no-resume --vlc /path` override them for one run.
- A missing VLC is explained with the install command (`sudo apt install vlc`, `brew install --cask vlc`).

### Improved
- The streaming banner leaves out the "saving to" line (nothing is saved), and "Continue watching" rows carry a
  ▶ instead of a download tick.

## 1.12.0
### New: the home dashboard
- `ytgrab` now opens a real dashboard: the big banner, an **input box with a live placeholder** (it cycles
  through hints such as "Search YouTube, paste a link, or choose an option…"), and a **list of options**:
  Download a link, Search YouTube, My channels, Pending queue, Watch clipboard, Library, Settings, Update.
- Use ↑ ↓ and Enter, **or** type: a number opens that option (the row lights up and the box says
  `→ My channels ⏎`), a link downloads, any other text searches. Old shortcuts (`s`, `p`, `w`, `t`, `f`, `d`, `Q`)
  still work. Badges show what is waiting (`3 waiting`, `12 saved`) and the last two downloads are listed.
- **Pressing Enter on an empty box no longer quits.** Quit with `q`, `Ctrl+D` or `Ctrl+C`.
- It adapts to the window: the banner shrinks to leave room for the options, details drop away on short
  windows, nothing wraps on narrow ones (phone-sized terminals included), and the box matches the banner width.
- Library (find files, duplicates, statistics, history, archive) and Update (check, install, preview, roll back)
  are menus of their own. Plain terminals and pipes keep the old one-line prompt.

### New: My channels (the "subscription suite", no YouTube account)
- Save creators **on this computer only** to explore later. Add by `@handle`, link, **or just a name** (it searches
  channels and lets you pick). Nothing is subscribed on YouTube and nothing is downloaded automatically: saved
  channels carry `auto: false`, so `ytgrab sync` and cron ignore them.
- Open a channel like a channel page, with tabs **Home · Videos · Shorts · Live · Playlists · Podcasts**
  (← → switch). A tab the channel does not have just says so. Playlists open to show their videos.
- Tick videos with Space (or `a` for all) and press Enter: one video downloads normally, several go through the
  usual batch planner. Videos you already have show ✓; ones you have not seen since your last visit show **NEW**.
  `n` in the list checks every saved channel for new uploads.
- Also available as `ytgrab channels`. The screens use the terminal's alternate screen, so your scrollback is
  left exactly as it was.

## 1.11.0
### New: a banner you can actually read
- The `large` banner is now a **big block-letter logo** drawn from a built-in pixel font with half-block
  characters: gradient letters, a soft drop shadow, and a frame that grows with your window instead of
  stopping at 70 columns. It picks the biggest of three sizes (`xl`, `large`, `wide`) that fits both the
  width **and** the height, so the menu underneath always keeps its room.
- The old smaller shadow letters are still available as **Classic** (Settings, banner size). Compact and
  minimal are unchanged and are still used automatically on very narrow windows.

### New: a calmer, safer updater (`ytgrab self-update`)
- The installer's own output is **captured** instead of printed over the animation (that interleaving was
  what made the old display flicker and tear). You see one stable two-line panel with a spinner, a progress
  bar, a percentage, the elapsed time and the installer's current step.
- The bar follows the **installer's real stages** (preparing, resolving, downloading, building, replacing the
  old version, installing, finishing). It creeps only a little between stages, never past what was reported,
  and reaches 100 % only when the install has really finished.
- If anything fails, the captured log is shown in a box, the previous version is restored through the same
  panel, and the exit code stays meaningful.
- **Ctrl+C is handled**: one press says it is finishing safely (a half-finished install can break the tool);
  a second press within 3 s aborts and rolls back.
- The cursor is always restored, lines never exceed the window width, and `--demo-animation` now previews the
  real panel against a scripted fake installer (still installing nothing).
- `ytgrab about` shows your real config folder.

## 1.10.0
### New: safe updater animation demo
- `ytgrab self-update --demo-animation` rapidly previews the real bucket-fill animation using a simulated installer process; it does not install packages, create a rollback snapshot or modify the current installation.
- In piped/non-interactive output, it explains that the preview needs a terminal and exits safely.

## 1.9.1
### Fixed: self-update animation color
- The bucket animation now uses the defined theme palette color, fixing an `AttributeError` in interactive terminals.
- Animation rendering is best-effort and cannot crash its background thread or interfere with the actual install.

## 1.9.0
### New: safe self-update preview and rollback
- `ytgrab self-update --preview [folder|zip]` shows installed/source versions, release notes, the install plan and snapshot safety without pulling or installing.
- Every actual update first creates a reinstallable snapshot. Failed installs trigger an automatic restore; `ytgrab self-update --rollback` restores the newest saved copy.
- Keeps the latest three rollback snapshots. Previewing a ZIP uses temporary extraction only.

### New: Home dashboard and creator feed
- The TUI opens on Home with saved creators, last-check times, recent downloads and active/finished/queued counts.
- Add creator handles/channels from Home; press Enter on a creator to check it, or use the all-creators update button. Existing `ytgrab sub` / `ytgrab sync` behavior and subscription settings remain intact.
- Quick actions lead to search and downloads. The Subscribers suite lists saved channels and provides Add, Remove, Explore, Check one and Check all actions. Enter/Explore opens a selectable list of recent videos; queue selected videos directly from the channel.

## 1.8.0
### New: quality-stamped videos and MP4/MKV choice
- Video filenames now include the actual downloaded resolution, e.g. `Kamata Official Video 1080p.mp4`.
- Choose MP4 or MKV in Settings → 29; MKV is remuxed as selected, MP4 is the default merge format, and audio is unchanged.

### Improved: concurrent downloads and self-update
- Batch rows identify the chosen quality/container and show percent, speed, and ETA when available.
- `ytgrab self-update` now animates a tap filling a bucket while the installer runs, then reports success/failure; piped output stays clean.


## 1.7.0
### New: speed and ETA on the progress bar
- The bar now shows the **download speed and the time left**: `████████░░░░  42%  7.4 MB/s  ETA 0:23`.
  The speed is smoothed so it doesn't jitter. The ETA covers the **whole** download (video + audio), is
  worked out from the pace of the last few seconds, and shows `--:--` until there is enough data.
- It still adapts to the window: when space runs out the ETA goes first, then the speed, then the bar shrinks;
  the percentage is always kept. When a download finishes it says how long it took (`100%  in 0:42`).
- Turn it off in Settings → 28 if you want the bar alone. It works with all eight bar styles.

### New: search shows the length, and you can pick several results
- Search results now put the **length right after the title** (`3:40`, `1:02:05`), so you can tell versions
  of a song or video apart. Columns adapt to your window, rows never wrap, and the time is never dropped.
- At the pick prompt you can now type **`1,2,6,7`**, a range **`4-7`**, a mix **`1,3-5,9`**, or **`all`**.
  You see the list with its total time, choose video or audio and quality once, and they all download together
  in the usual multi-download table. Videos you already have are skipped, with their location shown.
  One number works exactly as before.
- Full-screen UI (`ytgrab tui`): in the Search tab press **Space** to tick results (the cursor steps down),
  **a** to tick all, **Enter** to queue everything ticked (or just the highlighted one if nothing is ticked).
  Already-downloaded videos are skipped. The Time column now sits next to the title.

## 1.6.0
### New: progress bar styles (`ytgrab bar-style`, Settings → 27)
- Eight looks for the single download bar: **classic** (your theme), **fire**, **ocean**, **aurora**, **neon**,
  **sunset**, **matrix**, **minimal** (a thin line). Each has its own motion (embers flicker, waves roll,
  northern lights drift, neon pulses, green rain falls). `ytgrab bar-style` shows all of them animating at once
  so you can pick by eye; `ytgrab bar-style fire` sets one directly. All of them fit any window width.

### New: `ytgrab dupes`: clean up duplicate files (also `d` at the main prompt)
- Scans your download folder for **identical copies** (byte-for-byte), the **same video downloaded twice**
  (e.g. 720p and later 1080p, found through the history) and **same-title** files (ignoring playlist numbers,
  `(1)` and "copy"; flagged because it can be a different video).
- Shows how much space you would free, suggests which file to keep (the biggest, the one your history points
  at), and lets you review group by group. Nothing is deleted without a final yes.
- Files go to the **system trash**, never a hard delete. History entries that pointed at a removed file are
  re-pointed at the one you kept, so the "already downloaded" check keeps working.
- `--dry-run` only lists; `--auto` trashes only verified identical copies (re-checked with a full hash).

### New: `ytgrab find WORDS…`: search your library (also `f` at the main prompt)
- Searches your history (title, channel, link) **and** the files in your download folder, so videos you moved
  or copied in are found too. Accent- and case-insensitive; every word must match; titles rank above channels.
- Shows the location, size and date, flags files that no longer exist, and lets you open a file or its folder
  by number. `--audio` / `--video` / `-n` narrow it down.

### New: SponsorBlock levels and split-by-chapters
- SponsorBlock is no longer all-or-nothing: **off / sponsors only / sponsors + self-promo + reminders /
  everything** (Settings → 10, or `--sponsorblock LEVEL`). An old "on" setting keeps meaning "everything".
- **Split into chapter files** (Settings → 26, or `--split-chapters`): besides the full video you get a folder
  named after it with `001 - Intro.mp4`, `002 - …`. Works for audio downloads too and is saved in presets.

### Fixed
- Presets can now store the SponsorBlock level and the chapter split.

## 1.5.2
### New: it tells you when you already have a video
- Before downloading a single video, ytgrab checks your history and download folder. If you already have it you
  see **"Already downloaded" and the full location** (plus size and date), and can `[o]`pen the file, open its
  `[f]`older, `[d]`ownload it again anyway, or press Enter to skip. Nothing is downloaded unless you say so.
- It recognises the video, not the link: `youtu.be/…`, `watch?v=…&t=30s`, `/shorts/…` and links with a
  playlist attached all match. A file you renamed to another extension, or moved into a sub-folder of your
  download folder, is still found. Files that only match by title are flagged "matched by name only".
- If you deleted the file, you are told it is gone and the video downloads again (no pointless block).
- Batches, playlists, `get` and `sync` already skipped known videos; they now also list **where** the first few
  are. The Textual UI shows the same "already downloaded" dialog (Skip / Download again).
- History entries now store the video id, so old and new downloads are matched reliably. Turn the whole check
  off with Settings → "Skip already-downloaded".

## 1.5.1
Bug-fix release plus a new download display. Every fix has a regression test (`tests/test_v151.py`).

### New: one bar, nothing else
- While a video downloads you now see **a single animated progress bar** and its percentage. No speed/ETA
  text, no sparkline, no separate Video/Audio rows: the video and audio streams are folded into one bar that
  only ever moves forward.
- **It fits any window.** The width is recomputed every frame from the real terminal size (and follows live
  resizes), so it can never wrap: wide windows get a long bar, narrow ones a short bar, tiny ones just `42%`.
- Sub-cell precision (eighth blocks), the theme's gradient, a light that sweeps across the filled part, eased
  motion, a bouncing pulse while the size is still unknown, a flowing bar while ffmpeg merges/converts, and a
  green finished bar. Piped / cron / `NO_COLOR` output stays plain (a `40%` line every 10 %).
- yt-dlp's own error text no longer scribbles over the bar; ytgrab prints one clean error line instead.

### Fixed
- **The garbled, repeating progress bars** (1.5.0): the old line was a fixed ~78 cells wide, so in a narrower
  window every update wrapped and left a line behind (widening the window then reflowed them into a tiled mess).
- **A failed single download was recorded as a success**: history entry, archive entry (so it was skipped as
  "already downloaded" forever), a "Downloaded" notification, the post-download hook with no file, and removal
  from the pending queue in step mode. It now goes to the failed list (`ytgrab retry`) and nothing else.
- The quality menu used `height <=` for sizes and availability, so portrait videos (Shorts) showed the wrong
  resolution/size and greyed-out rows. It now uses the shorter side, like the download itself.
- A URL file saved with a BOM lost its first URL; one with bad bytes crashed. Both are read safely now.
- An invalid clip range silently downloaded the whole video; it now says why and asks again.
- The multi-download table could still wrap in very narrow windows (rows and footer are clipped to the width),
  and yt-dlp errors could print into it.
- `x` in the pending queue matched any input starting with "x"; watch mode ignored a link copied a second time
  after other clipboard changes; sizes said KB/MB but divided by 1024 (now decimal); bad `--min/max` style
  length filters gave a raw float error; a saved profile with empty values could blank settings.
- The TUI and a CLI run no longer overwrite each other's settings: only the keys a process changed are saved.

## 1.5.0
### New
- **Sync no longer misses uploads.** `ytgrab sync` pages back (15, 30, 60... up to `sync_max_depth`, default 300)
  until it reaches a video you already have, so a channel that posted 40 videos since your last sync is fully
  caught up. The very first sync of a subscription stays shallow, so it never bulk-downloads a back catalogue.
- **Per-subscription settings and filters**: `ytgrab sub set <# or name> KEY=VALUE ...`
  - `audio=mp3|m4a|opus|wav` or `video=best|1080|720|480|360` (a podcast channel can be audio-only, the rest video),
    `format=default` to undo
  - `min=MINUTES` / `max=MINUTES` (skip trailers, skip 3-hour streams)
  - `include=REGEX` / `exclude=REGEX` on the title (case-insensitive; quote it: `exclude='live|reaction'`)
  - `reset` clears everything. `sub list` shows each subscription's settings.
  - Filtered-out videos are marked as seen so they are not re-checked on every run. Videos with an unknown
    length (live, premieres) are kept. The TUI's sync follows the same rules.
- **`ytgrab sync --dry-run`**: lists what would be downloaded and what the filters skip. Changes nothing
  (no downloads, no timestamps, nothing marked as seen) and works while a real sync is running.
- **Failure log + `ytgrab retry`**: every failed or unreadable download is saved to `failed.json` with its reason
  and attempt count. `ytgrab retry` tries them again (exit code 1 if any still fail), `retry --list` shows them,
  `retry --clear` forgets them. A later success removes the entry; videos you got another way are dropped.
  `ytgrab doctor` shows the count. Works for CLI, `get`, `sync` and the TUI.

### Developer
- The test suite now runs against a throw-away HOME, so it can never touch your real `~/.config/ytgrab`.
- `JobRunner`/`batch_download` accept per-URL `overrides`; `subs.plan_sub()` is shared by the CLI and TUI.

## 1.4.1
Bug-fix release from a code review. Every fix has a regression test (`tests/test_regressions.py`).

### Security
- **`self-update` could install from PyPI**: a relative folder (`ytgrab self-update ytgrab`) became the bare
  spec `ytgrab[tui]`, which pip reads as a package name. The install spec is now always an absolute path.
- **`cookies.txt` is now private** (0600; config dir 0700). Older installs are fixed on the next run.
  `install.sh` backups are private too, and `ytgrab cookies-clear` also wipes the copies in config backups.
- Thumbnail downloads only follow `http(s)` URLs. No more `os.system("clear")`.

### Data safety
- **History, queue, subscriptions and config are written atomically** and a corrupt file is moved aside
  (`*.corrupt-<time>`) instead of being silently replaced by an empty one.
- **Cross-process locking** for history, archive, queue and subscriptions: the TUI and a cron `sync`
  no longer overwrite each other (the old code kept 6 of 150 entries under 6 concurrent writers).
- **Each download is recorded the moment it finishes**, not after the whole batch, so a crash or kill
  mid-sync no longer re-downloads everything next time.
- The pending queue only drops items that actually downloaded; failed/cancelled/unreadable ones stay.
- Overlapping `ytgrab sync` runs back off instead of fetching the same files twice;
  a video that appears in two subscriptions downloads once.

### Correctness
- `get` / `sync` exit non-zero when videos could not be read (private/deleted), and say which.
- **Shorts and portrait videos get the quality you asked for** (format sorting by `res` instead of a
  `height<=` filter that picked ~360p for a 720p/1080p request).
- New setting *Prefer h264/AAC* (on by default): at equal resolution you get the MP4 that plays in
  QuickTime, TVs and editors. It never lowers the resolution.
- Titles are capped at 150 bytes in filenames (no more "File name too long" with long Unicode titles).
- Empty exception messages no longer crash a download worker; errors show a readable first line.
- `-l 1.2.3M` is a clean error; a bare `100` is rejected instead of meaning 100 bytes/s. Clip times are validated.
- `-p NAME` now applies for this run only (it used to rewrite your saved settings).
- Batch/playlist info is fetched 4 at a time instead of one by one.
- Version comparison understands pre-releases; `git pull` in `self-update` can no longer hang on a password prompt.

### UI / ops
- The live table never wraps (adapts to terminal width, CJK-aware) and scrolls when there are more
  downloads than terminal lines.
- Piped output (cron logs) has no colour codes, spinner frames or cursor tricks; one line per finished file
  instead. Set `FORCE_COLOR=1` to override.
- `install.sh` installs the new version before removing anything old, warns when no JavaScript runtime
  (deno/node/bun) is installed, and keeps backups private.

## 1.4.0
### Install & update
- **`ytgrab self-update [folder | file.zip]`**: reinstall from the folder ytgrab came from (with
  `git pull --ff-only` when it tracks a remote), from another folder, or from a zip. `--check`,
  `--no-git`, `--reinstall`, `-y`. Refuses unsafe zips, asks before a downgrade, keeps the last 3 unpacked zips.
- **`ytgrab about`** and a line in `ytgrab doctor`: version, install method, source folder.
- **`install.sh`** rewritten: backs up `~/.config/ytgrab` first, removes old pipx / pip / hand-copied
  installs, installs with the TUI extra, verifies the result. `--dev` (editable), `--no-tui`, `--yes`.
- **`uninstall.sh`** (`--purge` also deletes your data, with confirmation).

## 1.3.0
### Banners
- **Large**: gradient block letters with a diagonal shimmer inside a rounded frame whose border runs
  along the theme gradient; tagline, divider, credit and version.
- **Compact**: one framed line, gradient wordmark, author and version.
- **Minimal (new)**: a single coloured line: wordmark, gradient rule, download folder, version.
- Pick a style in Settings → *Banner size*; preview with `ytgrab banner [style|all]`.
- Banners fall back automatically (large → compact → minimal) on narrow terminals, never clear the
  screen when output is piped, and respect `NO_COLOR`.
- Gruvbox gradient reworked (red → yellow) so the middle no longer turns grey.

### Fixed
- Colour interpolation returned one-shot generators instead of tuples (broke reusing a colour).

## 1.2.0
### New
- **`ytgrab tui`**: full-screen Textual interface with a live download table (pause / resume /
  cancel / retry / open), search with length filter, playlist picker, channel subscribe prompt,
  pending queue, subscriptions, history, theme switching and a quit confirmation.
  Optional dependency: `pip install 'ytgrab[tui]'` (the installer does this).

### Changed
- Download engine split into a UI-agnostic `JobRunner` shared by the CLI table and the TUI.
- Speed / ETA come from numeric fields (no raw ANSI codes); history and archive writes are thread-safe.

## 1.1.0
### New
- **Download archive**: playlists, batch files, `get` and `sync` skip videos already downloaded
  (`ytgrab archive`, `ytgrab archive clear`, Settings toggle).
- **Playlist folders**: `<download dir>/<playlist>/001 - Title.ext`.
- **Channel subscriptions**: `ytgrab sub add|list|remove` and `ytgrab sync`; adding a channel can mark
  its existing videos as seen so only new uploads are fetched.
- **`ytgrab get URL...`**: non-interactive download (playlists expanded, `-o DIR`, non-zero exit on failure).
- **Embedded subtitles, metadata and chapters** in videos; metadata/chapters in audio.
- **Retries** (configurable) with resume, and a **"Retry N failed?"** prompt after batches.
- **Post-download hook** with `{file}` and `YTGRAB_*` env vars; never goes through a shell.
- **Custom presets**: `ytgrab preset save|list|delete|NAME`; `-p` accepts them too.
- **Thumbnail preview** on the info card (chafa / timg / viu).
- **Search**: channel + views columns, 20 results, length filter (`f`).
- **Stats**: top channels and data per month.
- `NO_COLOR` / `TERM=dumb` support. `sync_depth` setting for how far back `sync` looks.

### Fixed
- Multi-download selection keys pointed at the wrong row when some items were skipped.
- Config defaults' nested dict was shared between loads.
- Output templates broke on download directories containing `%`.

## 1.0.1
### Security
- Fixed shell command injection: video titles/filenames were interpolated into
  `os.system()` strings (desktop notification, open file/folder). Now argv lists, no shell.

### Fixed
- SponsorBlock was silently a no-op (used a CLI-only option); now uses real postprocessors.
- `ytgrab doctor` crashed with NameError (missing import); now also checks ffmpeg and a JS runtime.
- `ytgrab update` / auto-update used `pipx upgrade yt-dlp`, which fails when yt-dlp is a
  dependency; now upgrades inside ytgrab's own environment.
- Playlist URLs opened very slowly (every video fully resolved); now flat extraction.
- Clipboard watch ignored shorts / live / embed links; stricter, host-based URL check.
- `strip_playlist_param` decoded percent-encoding; now re-encodes correctly.
- Clip ranges with end <= start are rejected.
- Batch/queue/playlist downloads recorded size 0 in history; real sizes now stored.
- opus/wav audio left a stray thumbnail file behind.
- Ctrl+C in a prompt quit the entire app; now goes back one level. Ctrl+C during a
  multi-download cancels cleanly.
- Jobs past #9 can now be selected with `j`/`k`.

### Housekeeping
- `queue.py` renamed `pending.py` (no longer shadows the stdlib module).
- Removed build artefacts, `__pycache__`, `main.py.save`, unused imports; added `.gitignore`.
- Depends on `yt-dlp[default]>=2025.1.1`; added `--version`; fixed README code fences.
- Added pytest regression suite.
