# ytgrab 1.9.0 — feature milestones

| Milestone | Scope | Status |
|---|---|---|
| 1. Safe update preview and rollback | Non-mutating update preview (version/change notes/plan), automatic pre-update snapshot, explicit rollback command, automatic restoration after failed install. | **Complete** |
| 2. Creator subscriptions | Surface saved YouTube channels/creators in Home; preserve subscription records, per-channel settings and sync semantics; enable one-creator or all-creator refresh. | **Complete** |
| 3. Home dashboard and release readiness | Home-first dashboard with activity summaries and quick actions; bump to 1.9.0, update docs, test and package. | **Complete** |

## Delivered behavior

- `ytgrab self-update --preview [folder|zip]` shows the installed/source versions, the new version’s changelog entries, planned installer command, and snapshot plan. Preview does not pull, persistently unpack, or install.
- Before replacing the current program, a reinstallable copy of the installed source (or installed package) is saved under `~/.local/share/ytgrab/rollback` or `$XDG_DATA_HOME/ytgrab/rollback`. The latest three are kept. Failed installs trigger an automatic restore attempt. `ytgrab self-update --rollback` restores the newest snapshot.
- The TUI opens to **Home**, showing saved creators, last-check dates, recent downloads, and session activity. Add a creator URL/handle; press Enter on a saved creator to check only it, or choose **Check creator updates** to sync all. The existing `ytgrab sub` and `ytgrab sync` commands, per-channel settings, and subscription store remain compatible.
- The **Subscribers** suite lists saved channels and offers dedicated Add, Remove, Explore, Check one and Check all actions. Enter/Explore opens recent videos in a multi-select queue dialog; removal is confirmed and only removes the subscription.
- Home quick actions lead to search and the downloads queue. Existing History, Search and Downloads tabs remain available.

## Verification

- Regression tests cover non-mutating preview, release notes, snapshot creation from both source and installed-package fallback, explicit rollback, automatic restore after installer failure, and the Home creator dashboard.
- Hotfix **1.9.1** corrects the updater animation color reference and makes animation rendering best-effort.
- Full suite including hotfix: **507 passed**.
- CLI version/help and source/package compile checks pass; release ZIP integrity is verified.
