# ytgrab 1.11.0 — Milestone 1 of 3: presentation and a trustworthy updater

| Milestone | Completion criteria | Status |
|---|---|---|
| 1. Big banner | Block-letter logo that scales with the window (width and height aware), gradient + shadow, graceful fallbacks, classic style kept as an option, reusable for other words (used by `ytstream` in milestone 3). | **Complete** |
| 2. Stable updater display | Installer output captured (no more interleaving with the animation); real stage-based progress; failure log box; automatic rollback through the same panel; safe Ctrl+C; cursor always restored; narrow-window safe. | **Complete** |
| 3. Verify and package | Updated banner/updater tests, new tests for both, full suite green, real pseudo-terminal run of the panel, release archive. | **Complete** |

## Try it

* `ytgrab` — the banner on the home screen (try a bigger / smaller window).
* `ytgrab self-update --demo-animation` — the real updater panel against a fake installer; installs nothing.
* Settings → banner size → *Classic* brings the old letters back.

## How the logo works

`theme.render_logo(word, sx, sy)` turns a 5x7 pixel font into text rows. Each terminal cell holds two vertical
pixels (`▀ ▄ █` with a foreground and a background colour), `sx` / `sy` set how many columns / half-rows one
font pixel covers, and a copy of the letters shifted by one pixel and darkened is the shadow. `pick_logo_size`
chooses `xl` (3x2), `large` (2x2) or `wide` (2x1) from the window, and `theme.banner(word=..., tagline=...)`
can draw any word made of the supported letters (`A B E G M R S T Y`, unknown characters become blanks).

## How the updater panel works

`updater_ui.run_installer` starts pip/pipx with its output piped, a reader thread turns the stream into lines
(splitting on `\r` as well, because pip redraws with it), `UpdateView.feed` maps them to stages, and a render
thread redraws two lines in place about 16 times a second. `FakeInstaller` is the scripted installer used by the
demo and by the tests.

## Honest limits

* The percentage is stage-based, not byte-based: pip does not report one. It is honest in the sense that it never
  jumps ahead of what was reported, but it is still an estimate between stages.
* If the installer prints nothing for a long time the panel keeps animating but the bar stops at its stage limit.
* Non-interactive runs (pipes, cron) deliberately keep the installer's normal output.

## Verification

* Full regression suite: **558 passed**.
* The updater panel was driven in a real pseudo-terminal at 100 and 40 columns; no frame wider than the window.
* Banner rendered at 188x48, 110x36 and 80x24 and inspected as images.
