# Third-Party Notices

The root MIT License applies only to original nx-toolbox source and documentation created for this repository. Bundled third-party works remain subject to their own copyright and licensing terms.

### 7-Zip

- Project: 7-Zip
- Bundled version: 24.09 (x64), released 2024-11-29
- Files: `tools/swap/bin/7z/7z.exe`, `tools/swap/bin/7z/7z.dll`
- Source: https://www.7-zip.org/
- License: GNU LGPL for most code, with BSD-licensed portions and an unRAR license restriction in parts of `7z.dll`
- License notice: `tools/swap/bin/7z/LICENSE.txt`

The bundled executables are used only for archive operations. The 7-Zip license notice must remain with redistributed binary copies.

### Packaged runtime

Release builds are produced with PyInstaller and include the Python runtime and these packages, each under its own license:

- Python (PSF License) - https://www.python.org/
- pywebview (BSD 3-Clause) - https://github.com/r0x0r/pywebview
- pythonnet and clr-loader (MIT) - https://github.com/pythonnet/pythonnet
- bottle, proxy_tools (MIT)
- Microsoft WebView2 loader, redistributed with pywebview under Microsoft's terms

The WebView2 runtime itself is not bundled; the program uses the one installed on the system.

### External packages and firmware

nx-toolbox can download or install packages from URLs configured in the source or by the user. Those downloaded files are not part of this repository and remain subject to their own copyright, licenses, and distribution terms. Their presence in a configured workflow does not mean this repository owns, audits, or endorses them.

This repository is unofficial and is not affiliated with, sponsored by, or endorsed by Nintendo. Nintendo, Nintendo Switch, Atmosphère, and related names and marks belong to their respective owners. Users are responsible for determining whether their intended use and redistribution comply with applicable terms and law.
