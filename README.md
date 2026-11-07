# ytgrab

A modern CLI YouTube downloader. Simple, fast, no GUI, no browser.

## Features

- A big block-letter banner that scales with your window, and a calm, safe updater with rollback
- **`ytstream`**: watch YouTube in VLC without downloading, with search, history and "continue where you left off"
- A home dashboard (type, or pick with the arrows) and **My channels**: save creators locally and explore
  their videos, shorts, live, playlists and podcasts, with no YouTube account
- Single video, playlists, batch files, paste-a-list queue
- Video filenames stamped with actual resolution (`1080p`); choose MP4 or MKV. Video (up to 4K, codec-aware) and audio (mp3/m4a/opus/wav, thumbnail embedded for mp3/m4a)
- Clipboard watch mode: collect URLs while browsing, download later
- YouTube search built in: paste a query instead of a URL
- Cookie support for age-restricted / 403-blocked content
- **SponsorBlock** with levels (sponsors only / + self-promo & reminders / everything) and optional
  **split-by-chapters** (one file per chapter, full video kept)
- **One animated progress bar** with speed + ETA that fits any window, in 8 looks: `ytgrab bar-style`
- **Search with lengths and multi-select**: results show the time next to the title; pick `1,2,6,7`, `4-7` or `all`
  and download them together
- **Already-downloaded check**: tells you where the file is instead of downloading it twice
- **`ytgrab dupes`**: find duplicate files (identical copies, same video twice, same title) and clean them up
  via the trash; **`ytgrab find`**: search everything you downloaded and open it
- 5 themes: youtube, nord, dracula, gruvbox, catppuccin, each with a gradient banner in three styles
  (**large**, **compact**, **minimal**); they shrink automatically on narrow terminals
- Download statistics dashboard (top channels, data per month), desktop notifications, speed limiter
- **Download archive**: playlists, batches, `get` and `sync` skip videos you already have
- **Playlist folders**: each playlist lands in its own numbered folder
- **Creator subscriptions**: save YouTube channels/handles with `ytgrab sub add @channel`, then fetch newer uploads with `ytgrab sync` or from the TUI creator feed
- **Subscribers suite** (`ytgrab tui` → Subscribers): see all saved channels, add a channel in-app, remove a selected subscription, explore recent channel videos and queue selected ones, or check one/all creators for new uploads
- **Home dashboard**: the TUI opens to saved creators, recent downloads, activity counts and shortcuts to sync, add creators, search and downloads
- **Per-channel settings and filters**: audio-only for podcasts, min/max length, title include/exclude,
  `ytgrab sync --dry-run` to preview
- **Failure log**: `ytgrab retry` re-runs whatever failed (including overnight cron failures)
- **Non-interactive mode**: `ytgrab get <url>...` with proper exit codes, for scripts
- Embedded subtitles, metadata and chapters; auto-retry + a "retry failed" prompt
- **Post-download hook**: run your own command on every finished file (no shell)
- Custom presets, thumbnail preview, search with channel / views / length filter
- `NO_COLOR` is respected
- **Full-screen TUI**: `ytgrab tui` (live download table, search, playlist picker, subscriptions, history)

## Install / update / uninstall

Linux/macOS users can use the Bash installer below (Debian/Ubuntu/Kali can install missing dependencies with `apt`). Native Windows 10/11 and Android Termux setup are described below; `install.sh` itself is for Linux/macOS/WSL.

```bash
cd ytgrab            # keep this folder: it is your source folder
./install.sh         # installs (and replaces any older install; backs up ~/.config/ytgrab first)
./install.sh --dev   # editable: edits to this folder run immediately, no update step
```

Updating your own tool after you add something (or receive a new version):

```bash
ytgrab self-update --demo-animation # safely preview the update animation; installs nothing
ytgrab self-update --preview       # review version, release notes and install/snapshot plan; makes no changes
ytgrab self-update                  # snapshot first, then re-install from your source folder (git pull first if it is a git repo)
ytgrab self-update --rollback      # restore the newest saved pre-update version
ytgrab self-update ~/other/ytgrab   # install from another folder
ytgrab self-update ytgrab-1_10_0.zip # install from a zip (kept in ~/.local/share/ytgrab/src)
ytgrab self-update --check          # only say whether the source folder is newer (read-only)
ytgrab about                        # version, install method, source folder, yt-dlp / textual versions
ytgrab update                       # separate: just upgrade yt-dlp
```

Typical loop: edit the code, bump `__version__` in `ytgrab/__init__.py` and `version` in `pyproject.toml`,
commit, run `ytgrab self-update`. A zip or git remote on another machine works the same way.
Your settings, history, archive and subscriptions are never touched by an update. Update snapshots are saved under `~/.local/share/ytgrab/rollback` (or `$XDG_DATA_HOME/ytgrab/rollback`); the newest three are retained.

```bash
./uninstall.sh          # remove the program, keep your data
./uninstall.sh --purge  # also delete ~/.config/ytgrab (asks first)
```

### Windows 10/11

1. Install Python 3.9 or newer from [python.org](https://www.python.org/downloads/windows/) and enable the Python launcher (`py`).
2. Extract the ytgrab ZIP, open PowerShell in the extracted `ytgrab` folder, then run:

   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   .\install-windows.ps1
   ```

3. Open a new Windows Terminal/PowerShell window and run `ytgrab --version`.

The installer creates an isolated environment under `%LOCALAPPDATA%\ytgrab\venv` and adds its `Scripts` directory to your user PATH. Install FFmpeg and Deno or Node.js separately and put them on PATH for merging/audio extraction and YouTube's JavaScript challenges. Install desktop VLC for `ytstream`; its default install locations are detected, or set the executable in Settings. Clipboard watch uses PowerShell's `Get-Clipboard`. Windows config and update data are stored under `%APPDATA%\ytgrab` and `%LOCALAPPDATA%\ytgrab`. Duplicate cleanup moves files to ytgrab's private `trash` folder, not the Windows Recycle Bin. Windows console UI works best in Windows Terminal. To uninstall, run `.\uninstall-windows.ps1` in PowerShell; it removes the PATH entry and asks before removing the virtual environment. Settings under `%APPDATA%\ytgrab` are kept.

### Android Termux

Install [Termux from F-Droid or GitHub](https://termux.dev/), download and extract the project ZIP in Termux, `cd` into its `ytgrab` directory, then run:

```sh
bash install-termux.sh
```

The installer uses Termux `pkg` and a virtual environment under `$PREFIX/opt/ytgrab`; it does not need root or `pipx`. It installs Python, FFmpeg and Node.js. For Android shared storage, run `termux-setup-storage`, approve the Android permission prompt, then choose the exposed folder, for example `ytgrab set-dir ~/storage/downloads`. Downloads to Termux's private home do not require shared-storage permission. Clipboard watch, opening saved files, and notifications can use the optional `termux-api` package plus the matching Termux:API Android app. Stock Termux does not provide desktop VLC's remote-control interface, so `ytstream` is not supported by default. Android's shared-storage permissions may limit atomic moves and duplicate cleanup; keep important originals backed up.

## Usage

```bash
ytgrab                                # interactive main loop (URL or search)
ytgrab search <query>                 # search and pick a video
ytgrab watch                          # watch clipboard for YouTube URLs
ytgrab playlist <url>                 # download a playlist
ytgrab batch urls.txt                 # batch from a file
ytgrab queue                          # paste many URLs
ytgrab stats                          # statistics dashboard
ytgrab settings                       # settings menu
ytgrab doctor                         # diagnostics (ffmpeg, JS runtime, yt-dlp, cookies)
ytgrab theme dracula                  # switch theme
ytgrab banner [large|compact|minimal] # preview the banner styles (default: all three)
ytgrab cookies-import ~/cookies.txt   # import browser cookies
ytgrab cookies-help                   # cookie export instructions
ytgrab tui                            # full-screen terminal UI (needs Textual)
ytgrab get <url>... [-o DIR]          # non-interactive; e.g.  ytgrab -a mp3 get <url>
ytgrab sub add @channel               # subscribe (offers to mark old videos as seen)
ytgrab sub list / sub remove <# or name>
ytgrab sub set <# or name> audio=mp3 min=10 exclude='live|reaction'   # per-channel format + filters
ytgrab sync                           # download new uploads from all subscriptions
ytgrab sync --dry-run                 # show what would download; changes nothing
ytgrab retry [--list | --clear]       # retry downloads that failed earlier
ytgrab archive [clear]                # show / reset the downloaded-videos archive
ytgrab find kendrick lamar            # search history + download folder, then open the file or folder
ytgrab find rain --audio -n 10        # audio only, at most 10 results
ytgrab dupes                          # find duplicate files, review them, move extras to the trash
ytgrab dupes --dry-run                # only list them; changes nothing
ytgrab dupes --auto                   # trash verified byte-identical copies without asking
ytgrab bar-style                      # preview all progress-bar looks (animated) and pick one
ytgrab bar-style fire                 # set one directly: classic fire ocean aurora neon sunset matrix minimal
ytgrab get URL --sponsorblock standard --split-chapters    # for this run only
ytgrab preset list | save NAME | delete NAME | NAME
ytgrab update                         # upgrade yt-dlp inside ytgrab's environment
ytgrab self-update                    # update ytgrab itself (see Install / update / uninstall)
ytgrab about                          # version + install details
ytgrab history / clear-history
ytgrab --version
```

Speed limits need a unit: `2M`, `500K`, `1.5MB/s`.

Search results: `f` filters by length (`10-60`, `>20`, `<5` minutes).

In-app shortcuts: `s` settings · `Q` paste-queue · `w` watch · `t` stats · `p` pending queue · `q` quit.
`Ctrl+C` goes back one level; at the main prompt it quits.

While downloading several items: `1-9` or `j`/`k` select, `p` pause, `r` resume, `c` cancel, `0` clear selection.

## TUI

`ytgrab tui` opens a full-screen interface that shares everything with the CLI (settings, archive,
history, hooks, subscriptions). Install Textual with `./install.sh` (it installs `ytgrab[tui]`),
or `pipx inject ytgrab textual`.

Paste a video, playlist or channel URL, or type a search, into the box at the top and press Enter.
Playlists open a picker; channels offer *subscribe* / *get latest*. Press `Esc` to leave the box so the
shortcut keys work:

| Key | Action | Key | Action |
|---|---|---|---|
| `/` | focus the input box | `r` | retry failed |
| `1`-`5` | switch tab | `x` | clear finished |
| `p` | pause / resume | `o` | open file / folder |
| `c` | cancel | `m` | video / audio |
| `s` | sync subscriptions | `t` | cycle theme |
| `Enter` | download the selected result / pending item | `Delete` | remove row |
| `Ctrl+Q` | quit (asks if downloads are running) | | |

## Subscription settings

```bash
ytgrab sub set 2 audio=mp3                 # this channel is always audio (mp3/m4a/opus/wav)
ytgrab sub set 2 video=720                 # ...or always this video quality
ytgrab sub set 2 min=10 max=90             # only videos 10-90 minutes long (min=off removes a limit)
ytgrab sub set 2 include=tutorial exclude='live|reaction|shorts'   # title regexes, case-insensitive
ytgrab sub set 2 format=default            # back to your global format
ytgrab sub set 2 reset                     # clear all of the above
ytgrab sync --dry-run                      # preview the result
```

Filters apply to future uploads. Videos they skip are marked as seen (so they are not re-checked every run);
videos with no known length (live streams, premieres) are never dropped by `min`/`max`.
`sync` looks back `sync_depth` (15) uploads at first, then keeps paging until it reaches a video you already
have, up to `sync_max_depth` (300).

## Post-download hook

Settings → *Video container (MP4 / MKV)* to choose a video output container; *Post-download hook*. The command runs after every finished file, **without a shell**.
`{file}` is replaced by the saved path (it is appended if you leave it out). Title, URL and kind are
passed as environment variables `YTGRAB_TITLE`, `YTGRAB_URL`, `YTGRAB_KIND` (plus `YTGRAB_FILE`).

```
mv {file} /media/music
notify-send Done {file}
```

## Automatic sync (cron)

```
0 */6 * * *  /home/you/.local/bin/ytgrab sync >> ~/ytgrab-sync.log 2>&1
```

Logs stay plain text (no colours or animation when piped), the exit code is non-zero if anything failed or
could not be read, and a second `sync` that starts while one is still running exits quietly.

## Config

Everything lives in `~/.config/ytgrab/` (private, mode 0700): `config.json`, `history.json`, `queue.json`, `archive.txt`, `subs.json`, `failed.json`, and an optional `cookies.txt` (0600: it holds your session cookies, so treat backups like passwords).
Writes are atomic; a damaged file is kept as `<name>.corrupt-<time>` instead of being overwritten. Safe to back up. Safe to delete.

## Development

```bash
pip install -e '.[dev]'
pytest
```

