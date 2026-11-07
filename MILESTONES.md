# ytgrab 1.8.0 — upgrade milestones

**Baseline chosen:** `ytgrab-1_7_0-patched.zip`, the newest of the supplied archives. It includes the 1.7.0 features and additional fixes for TUI and regression behavior.

| Milestone | Scope | Status |
|---|---|---|
| 1. Safer, clearer video outputs | Stamp the actual selected video resolution in video filenames; restrict video container settings to MP4/MKV; preserve audio naming and validate old/invalid settings. | **Complete** |
| 2. Premium multi-download experience | Keep concurrent rows readable and terminal-safe, show quality/container, percent, speed and ETA; expose MP4/MKV in the TUI; keep search/watch batch quality selection consistent. | **Complete** |
| 3. Animated self-update and release readiness | Add the terminal-aware filling-bucket update animation; bump version, update docs and regression tests; verify and package the source. | **Complete** |

## Delivered behavior

- Video filenames include the selected video resolution when available, for example `Diamond Platnumz Kamata Official Video 1080p.mp4`. If the source has no video height, yt-dlp omits the resolution rather than writing a misleading `NAp` stamp. Audio filenames remain unchanged.
- Settings → **29. Video container (MP4 / MKV)** and the TUI selector provide exactly two video-container choices. MP4 is the default; MKV output is remuxed to MKV. Invalid legacy values safely fall back to MP4.
- Multi-download rows prioritize the quality/container badge so it remains visible on narrow terminals; in-progress rows display percentage, speed and ETA when there is room. The TUI has a dedicated quality/container column alongside its live progress bars.
- `ytgrab self-update` animates the tap and filling bucket only on an interactive terminal while the installer runs. It avoids fake percentages and reports success only after the installer exits successfully; piped sessions remain plain.

## Verification

- Full test suite: **494 passed**.
- Python source and tests compile successfully.
- Release bundle is built from the patched 1.7.0 project tree with the 1.8.0 source, tests, install/uninstall scripts, README and changelog.
