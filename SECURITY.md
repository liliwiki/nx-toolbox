# Security Policy

nx-toolbox manages removable-storage content and can delete, overwrite, copy, download, extract, and install files. It is not an official Nintendo utility and cannot guarantee compatibility with every console setup, firmware version, SD-card layout, or third-party package.

## Reporting a vulnerability

1. Do not publish console keys, credentials, personal saves, device identifiers, private SD-card data, or working exploit details in a public Issue.
2. Prefer [GitHub private vulnerability reporting](https://github.com/Lili679280/nx-toolbox/security/advisories/new) when available.
3. Include the affected nx-toolbox version, module, Windows version, reproduction conditions, expected impact, and a minimal non-sensitive log.
4. For vulnerabilities in 7-Zip, Atmosphère, firmware packages, or another upstream component, also report the issue to the appropriate upstream project.

## Operational safety

- Keep a complete independent backup before using cleanup, package installation, firmware installation, or restore operations.
- Verify the selected drive, preview, whitelist, archive, download source, and firmware compatibility before confirming an operation.
- Treat downloaded packages as untrusted until their source and contents have been reviewed.
- Do not rely on snapshots as the only backup; they may reside on the same failing storage device.
- Review source changes and release notes before updating the application.

The MIT License provides the software without warranty. Users remain responsible for their data, hardware, package sources, and compliance obligations.
