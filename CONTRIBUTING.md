# Contributing

Contributions are welcome when they add a focused tool, improve safety, or make an existing module more reliable without turning the launcher into one tightly coupled application.

Before submitting a change:

1. Keep each tool independently understandable, with one clear entry point and responsibility.
2. Preserve the launcher as a neutral toolbox; do not make one module define the whole project.
3. Never commit credentials, console keys, personal saves, private notes, local paths, build output, or unrelated binaries.
4. Add a preview or explicit confirmation before destructive file operations.
5. Keep `version.json`, `sd_cleaner/common.py`, release notes, and the deployed update manifest consistent when preparing a release.
6. Explain which files changed, what user-facing behavior changed, and how the result was verified.
7. Record every new third-party component, exact version, source, and license in `THIRD_PARTY_NOTICES.md`.

## Commit Convention

Use Conventional Commits with a concise English description:

```text
type(scope): short description
```

Common types are `feat`, `fix`, `docs`, `chore`, `refactor`, and `test`.

| Scope | Use for |
|---|---|
| `toolbox` | Launcher, tool registry, or shared toolbox behavior |
| `sd-card` | SD-card package tool and its modules |
| `update` | Version checks and application updates |
| `build` | PyInstaller packaging and release artifacts |
| `readme` | English or Chinese project overview |
| `project` | Repository policy, templates, and licensing |
| `release` | Version metadata, changelog, and release notes |

Examples:

```text
feat(toolbox): add a new tool card
fix(sd-card): correct whitelist path handling
docs(readme): update bilingual project overview
docs(release): add v1.1.0 release notes
```

Keep each commit focused on one logical change.
