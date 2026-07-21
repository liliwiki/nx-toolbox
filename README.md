# nx-toolbox

[![Windows Toolbox](https://img.shields.io/badge/Windows-Toolbox-111827)](https://github.com/Lili679280/nx-toolbox)
[![Latest Release](https://img.shields.io/github/v/release/Lili679280/nx-toolbox?display_name=tag&label=release)](https://github.com/Lili679280/nx-toolbox/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**English** | [简体中文](README.zh-CN.md)

An extensible Windows toolbox and unified launcher for Nintendo Switch utilities. nx-toolbox provides one consistent home for focused tools while allowing new modules to be added over time without turning each workflow into a separate application.

> This repository is an unofficial community project. It is not affiliated with, sponsored by, or endorsed by Nintendo.

## Quick Start

Download `nx-toolbox-v1.0.0.exe` from the [latest release](https://github.com/Lili679280/nx-toolbox/releases/latest), or clone and run the toolbox directly:

```powershell
git clone https://github.com/Lili679280/nx-toolbox.git
Set-Location nx-toolbox
python nx_toolbox.py
```

Python 3.11 or later is recommended. Tkinter is included with the standard Windows Python installer. The optional `windnd` package enables drag-and-drop support in compatible modules.

Release executables are currently unsigned. Verify the SHA-256 value published on the Release page before running a downloaded file.

## Why nx-toolbox Exists

Switch-related desktop utilities often solve one narrow task and ship with different interfaces, update paths, and safety behavior. nx-toolbox provides a shared launcher and repository so those tools can grow as a coherent collection.

- One launcher for present and future tools
- Independent modules with focused responsibilities
- Shared release, update, logging, and packaging conventions
- A consistent Windows interface instead of unrelated standalone programs
- Public source that can be reviewed before it touches SD-card data

## How It Works

1. `nx_toolbox.py` presents the toolbox launcher and available tool cards.
2. Each tool remains an independent Python entry point or module package.
3. Shared helpers provide configuration, archives, logging, snapshots, and version handling where needed.
4. `update_checker.py` and `updater.py` handle application updates without coupling that process to one tool.
5. New utilities can be added as additional modules and launcher cards while preserving existing workflows.

## Current Tools

| Tool | Status | Purpose |
|---|---|---|
| nx-toolbox launcher | Available | Unified entry point for the growing tool collection |
| SD Card Package Tool | Available | Review, preserve, clean, back up, and update selected SD-card content |
| Additional utilities | Planned | Future tools will appear as independent modules and launcher cards |

The SD Card Package Tool is the first implemented direction inside nx-toolbox. It is not the boundary of the project: the toolbox is intentionally structured to host more tools later.

## SD Card Package Tool

The current module can:

- Preserve emuMMC, Nintendo data, saves, cheats, mods, themes, and custom paths through a whitelist
- Preview cleanup targets before changing files
- Create snapshots before cleanup or package updates and restore from those snapshots
- Install Atmosphère packages from local archives or configured online sources
- Detect Atmosphère and firmware versions and install local or online firmware packages
- Record important operations and display progress and download speed

## Safety Boundaries

nx-toolbox can delete, overwrite, copy, and install files on removable storage. Always keep an independent backup, verify the selected drive and package source, and review the preview before continuing. Snapshots reduce risk but are not a substitute for a full backup.

Do not publish console keys, personal saves, credentials, device identifiers, or private SD-card contents in Issues. Security-sensitive reports should follow [SECURITY.md](SECURITY.md).

## What's Included

| Component | Purpose |
|---|---|
| `nx_toolbox.py` | Toolbox launcher and tool registry |
| `SD_Cleaner.py` | Standalone entry point for the current SD-card tool |
| `sd_cleaner/` | SD-card operations, archives, packages, snapshots, logging, and version detection |
| `update_checker.py` | Version checks and update download workflow |
| `updater.py` | Separate Windows executable replacement process |
| `build_exe.py` | PyInstaller build orchestration |
| `version.json` | Public release metadata and update manifest source |
| `7z/` | Bundled 7-Zip 24.09 runtime used for archive extraction |

## Repository Layout

```text
nx-toolbox/
├── nx_toolbox.py
├── SD_Cleaner.py
├── sd_cleaner/
├── update_checker.py
├── updater.py
├── build_exe.py
├── version.json
├── 7z/
├── docs/release-notes/
├── README.md
└── README.zh-CN.md
```

## Development and Building

Run the launcher directly:

```powershell
python nx_toolbox.py
```

Build the Windows executables:

```powershell
python -m pip install pyinstaller
python build_exe.py
```

Build artifacts are written to the ignored `out/` directory. Executables belong in GitHub Releases or the configured download service, not in the source tree.

## Versioning and Updating

`version.json` is intentionally tracked because it is public release metadata, contains no secrets, and is bundled by the build process. A release must keep these locations consistent:

1. `version.json` in this repository
2. `VERSION` in `sd_cleaner/common.py`
3. The deployed update manifest served to existing clients

The repository follows [Semantic Versioning](https://semver.org/), and notable changes are recorded in [CHANGELOG.md](CHANGELOG.md).

## Contributing

Contributions that add focused tools, improve safety, or make existing modules more reliable are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Keep each tool independently understandable and avoid coupling the whole launcher to one module.

## License and Third-Party Material

Original nx-toolbox source and documentation are available under the [MIT License](LICENSE). Bundled third-party works retain their own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [7z/LICENSE.txt](7z/LICENSE.txt).

Nintendo, Nintendo Switch, Atmosphère, and related names and marks belong to their respective owners. Users are responsible for ensuring that their use of this software, firmware files, update packages, and bundled components complies with applicable terms and law.
