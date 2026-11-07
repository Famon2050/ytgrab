# ytgrab 1.14.0 — Windows and Termux compatibility

## Goal
Provide native installation and practical CLI/download workflows for Windows 10/11 and Android Termux while preserving the existing Linux/macOS install path.

## Delivered
- PowerShell installer creates an isolated per-user Python environment and adds its command scripts to PATH.
- Termux installer uses `pkg` and a private virtual environment without root or pipx.
- Windows gets native one-key dashboard/download input, process locks, app-data directories, clipboard access and VLC discovery.
- Termux gets clipboard, open-file and notification hooks when Termux:API is available, with clear shared-storage guidance.
- README distinguishes shared Android storage permissions and optional/unsupported desktop VLC integration.

## Validation
- Python syntax compilation succeeds.
- Automated suite passes in the Linux sandbox; Windows and Android runtime validation still requires the target platforms.
