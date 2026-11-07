# ytgrab 1.13.0 — Milestone 3 of 3: `ytstream`, watch in VLC without downloading

| Milestone | Completion criteria | Status |
|---|---|---|
| 1. Streaming engine | Resolve direct stream URLs with yt-dlp (video+audio or combined, live), build a safe VLC command line, locate VLC, never touch the disk. | **Complete** |
| 2. Remembered position | Read the playback position from VLC's rc interface while it plays, save it when VLC closes, offer to resume, mark watched, keep a bounded history. | **Complete** |
| 3. The `ytstream` mode | Own command and dashboard (search, link, continue, history, channels, settings), now-playing panel with controls, entry from `ytgrab` and `ytgrab stream`. | **Complete** |
| 4. Verify and package | Unit tests, a real socket server for the rc protocol, a stand-in VLC process for end-to-end runs, pseudo-terminal runs, full suite green. | **Complete** |

## Try it

* `ytstream` — the streaming dashboard. `ytstream lofi hip hop radio`, or `ytstream https://youtu.be/...`.
* Close VLC part-way, run `ytstream` again and open **Continue watching**: it offers to resume at that minute.
* While playing: `space` pause, `← →` seek 10 s, `q` stop. Settings → quality cap / fullscreen / VLC path.
* Needs VLC (`sudo apt install vlc`, or `brew install --cask vlc`).

## How it works

1. `stream.resolve` calls yt-dlp with `skip_download`, a quality-capped format (`bestvideo*[height<=Q]+bestaudio/...`),
   no playlist expansion and no speed limit, then `streams_from_info` picks the video URL and the audio URL.
2. `stream.vlc_command` builds an argument list (never a shell string): `--play-and-exit`, `--meta-title`,
   `--start-time`, `--http-user-agent`, the video URL, `--input-slave=<audio URL>`, and `--extraintf=rc
   --rc-host=127.0.0.1:<free port>` so ytgrab can talk to VLC.
3. `PlaySession` starts VLC and a tracker thread that connects to the rc socket and asks `get_time` / `get_length`
   twice a second. The last answer is what is saved when VLC exits, so closing VLC (or ytgrab) never loses more than
   about half a second of position.
4. `stream_history.json` (in the ytgrab config folder) stores id, title, channel, position, duration, watched time and
   count. A position below 15 s counts as "just started", and 95 % or more counts as watched; neither offers a resume.

## Honest limits

* I could not reach YouTube or run a real VLC where this was built. The yt-dlp step was tested with scripted
  responses, and the rc protocol and playback loop were tested against a real socket server and a stand-in process
  that speaks VLC's remote-control protocol. **Please try one real video**; if VLC rejects an option in your version,
  the panel says VLC closed straight away, and *Settings → Extra VLC options* lets you adjust.
* Position tracking relies on VLC's `rc` interface. If your VLC has it disabled, playback still works but your place
  cannot be saved (the panel says so).
* Stream URLs from YouTube expire after a few hours; a paused session left overnight may need to be reopened.
* Linux and macOS (VLC path detection covers both). Windows and Android/Termux are not part of this work.

## Verification

* Full regression suite: **736 passed**; 78 new tests for this milestone.
* The dashboard and the now-playing panel were driven in a real pseudo-terminal and inspected as images.
