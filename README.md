# nx-toolbox

[![Windows Toolbox](https://img.shields.io/badge/Windows-Toolbox-111827)](https://github.com/Lili679280/nx-toolbox)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**English** | [简体中文](README.zh-CN.md)

Maintenance tools for Nintendo Switch. A launcher plus independent tools; the package swap tool is the one available today. The interface is in Simplified Chinese.

> This is the 2.0 development branch and its releases are previews. The 1.0 source is on the [main branch](https://github.com/Lili679280/nx-toolbox/tree/main).
>
> This repository is an unofficial community project. It is not affiliated with, sponsored by, or endorsed by Nintendo.

## Download

Get `NXToolbox-<version>.exe` from the [Releases](https://github.com/Lili679280/nx-toolbox/releases) page and run it to install. You can choose the install location and shortcuts; it installs for the current user only and does not need administrator rights. To uninstall, run the installer again or use "Installed apps" in Windows Settings.

Requires Windows 10 or 11 with the WebView2 runtime (built into Windows 11). The executable is unsigned. Verify the SHA-256 published on the Release page before running it.

## Package swap tool

Replaces the Atmosphère package on a storage card with a new one.

Three rules, checked in order:

1. Anything you ticked or unticked by hand wins
2. Paths on the blacklist are removed
3. Paths on the whitelist are kept

Everything else is removed, then the new package is installed.

- See which files will change before you start, folder by folder
- Online packages are listed without downloading the whole archive
- A snapshot taken before the update lets you restore removed and overwritten files
- Every written file is verified
- Reads `whitelist.txt` and `blacklist.txt` shipped in the package or on the card
- Firmware download and copy

The built-in lists are in [tools/swap/data](tools/swap/data).

## Safety

The swap tool deletes and overwrites files on the storage card. Confirm you picked the right card and review the list before updating. Snapshots reduce risk but do not replace a full backup; the Nintendo and emuMMC folders are too large to be included in snapshots.

Do not post console keys, certificates, saves, or device identifiers in public issues. Report security problems privately as described in [SECURITY.md](SECURITY.md).

## Layout

```text
host.py            Host: one program that starts as the launcher or as a tool
bootstrap.py       Single-file installer
setup/             Install and uninstall screens
nxhost/            Shared by the launcher and tools (settings, log, update, program detection)
launcher/          Launcher page
ui/                Shared styles and scripts
tools/swap/        Package swap tool (core/ is the backend, index.html is the page)
build.py           Build and self-test
```

Each tool is a folder with a `tool.json`. The launcher reads only that description and starts the tool as a separate process; tools that need administrator rights are elevated on their own at launch.

The interface is web content running in [pywebview](https://pywebview.flowrl.com/) on the system WebView2; the logic is Python.

## Development

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install pywebview pyinstaller
.venv\Scripts\python host.py                 # launcher
.venv\Scripts\python host.py run swap        # swap tool
.venv\Scripts\python host.py dev swap        # debug the page in a browser
.venv\Scripts\python build.py                # build into dist/ and release/
```

Set `NXTOOLBOX_FAKE_SD=<folder>` to treat an ordinary folder as a storage card while debugging.

## Versioning

The version is in `app.json`. Releases follow [Semantic Versioning](https://semver.org/); changes are recorded in [CHANGELOG.md](CHANGELOG.md).

## License and third-party content

Original source and documentation are under the [MIT License](LICENSE). Third-party content keeps its own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Nintendo, Nintendo Switch, Atmosphère, and related names and marks belong to their respective owners.
