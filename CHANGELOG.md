# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0-preview.1] - 2026-10-13

A rewrite. Preview release.

### Changed

- New interface built on pywebview and WebView2, replacing the Tkinter launcher and wizard.
- The launcher and each tool now run as separate processes from one program.
- Swap rules reduced to three: manual choices, then the blacklist, then the whitelist; everything else is removed before the new package is installed.
- Distributed as a single-file installer that unpacks into `%LOCALAPPDATA%\Programs\NXToolbox`.

### Added

- File-level preview of what an update will change, browsable by folder.
- Listing of online packages without downloading the archive.
- Verification of every file written to the card.
- Support for `whitelist.txt` and `blacklist.txt` shipped in the package or on the card.
- Online update with SHA-256 verification.

### Removed

- Retro-style launcher and sound effects.
- Drag-and-drop of archives onto the window.

## [1.0.0] - 2026-06-12

### Added

- Extensible Windows toolbox launcher for present and future Switch utilities.
- Initial SD Card Package Tool with whitelist-based cleanup previews.
- Package and firmware installation from local or configured online sources.
- Snapshot backup and restore workflow for update operations.
- Atmosphère and firmware version detection.
- Operation logging, download progress, and application update workflow.
- Separate English and Simplified Chinese project documentation.
- Contribution guidelines, security policy, Issue templates, and Pull Request template.

[Unreleased]: https://github.com/Lili679280/nx-toolbox/compare/v2.0.0-preview.1...v2
[2.0.0-preview.1]: https://github.com/Lili679280/nx-toolbox/releases/tag/v2.0.0-preview.1
[1.0.0]: https://github.com/Lili679280/nx-toolbox/releases/tag/v1.0.0
