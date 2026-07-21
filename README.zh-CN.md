# nx-toolbox

[![Windows 工具箱](https://img.shields.io/badge/Windows-Toolbox-111827)](https://github.com/Lili679280/nx-toolbox)
[![最新版本](https://img.shields.io/github/v/release/Lili679280/nx-toolbox?display_name=tag&label=release)](https://github.com/Lili679280/nx-toolbox/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB)](https://www.python.org/)
[![许可证：MIT](https://img.shields.io/badge/许可证-MIT-green.svg)](LICENSE)

[English](README.md) | **简体中文**

一个可持续扩展的 Nintendo Switch Windows 工具箱与统一启动器。nx-toolbox 为不同用途的实用工具提供一致入口，并允许后续不断加入新模块，而不必把每项工作都拆成互不相关的独立程序。

> 本仓库是非官方社区项目，与 Nintendo 没有隶属、赞助或认可关系。

## 快速开始

从[最新 Release](https://github.com/Lili679280/nx-toolbox/releases/latest)下载源码压缩包，或者克隆仓库后直接运行：

```powershell
git clone https://github.com/Lili679280/nx-toolbox.git
Set-Location nx-toolbox
python nx_toolbox.py
```

建议使用 Python 3.11 或更高版本。Windows 官方 Python 安装程序通常已包含 Tkinter；可选的 `windnd` 包可为兼容模块提供拖放支持。

## 为什么需要 nx-toolbox

Switch 相关桌面工具往往只解决单一问题，并分别使用不同的界面、更新方式和安全策略。nx-toolbox 提供统一启动器与共同仓库，让这些工具能够作为一个清晰、连贯的集合持续发展。

- 用一个启动器承载当前和未来的工具
- 每个模块保持独立、职责明确
- 共享发布、更新、日志和打包规范
- 统一 Windows 使用体验，减少零散独立程序
- 对可能修改 SD 卡数据的逻辑提供可审查源码

## 工作方式

1. `nx_toolbox.py` 展示工具箱主页与可用工具卡片。
2. 每个工具保持独立 Python 入口或模块包。
3. 有需要时复用配置、压缩包、日志、快照和版本处理能力。
4. `update_checker.py` 与 `updater.py` 独立处理应用更新，不与某一个工具强绑定。
5. 后续工具可以通过新模块和新卡片加入，同时保留已有工作流。

## 当前工具

| 工具 | 状态 | 用途 |
|---|---|---|
| nx-toolbox 启动器 | 可用 | 工具集合的统一入口 |
| SD 卡换包工具 | 可用 | 检查、保留、清理、备份并更新指定 SD 卡内容 |
| 更多工具 | 规划中 | 后续作为独立模块和工具卡片加入 |

SD 卡换包工具是 nx-toolbox 当前第一个已经实现的方向，但不是项目边界。这个仓库从一开始就按继续容纳更多工具的方式组织。

## SD 卡换包工具

当前模块可以：

- 通过白名单保留 emuMMC、Nintendo 数据、存档、金手指、MOD、主题和自定义路径
- 在修改文件前预览清理目标
- 在清理或更新整合包前创建快照，并从快照恢复
- 从本地压缩包或配置的在线来源安装 Atmosphère 整合包
- 检测 Atmosphère 与固件版本，并安装本地或在线固件包
- 记录关键操作，显示处理进度和下载速度

## 安全边界

nx-toolbox 可以删除、覆盖、复制以及向移动存储安装文件。请始终保留独立备份，确认所选磁盘与安装包来源，并在继续前检查操作预览。快照可以降低风险，但不能替代完整备份。

不要在公开 Issue 中上传主机密钥、个人存档、账号凭据、设备标识或 SD 卡隐私内容。安全问题请按照 [SECURITY.md](SECURITY.md) 私下报告。

## 内置内容

| 组件 | 用途 |
|---|---|
| `nx_toolbox.py` | 工具箱启动器与工具注册入口 |
| `SD_Cleaner.py` | 当前 SD 卡工具的独立入口 |
| `sd_cleaner/` | SD 卡操作、压缩包、整合包、快照、日志与版本检测 |
| `update_checker.py` | 版本检查与更新下载流程 |
| `updater.py` | 独立的 Windows 程序替换流程 |
| `build_exe.py` | PyInstaller 构建脚本 |
| `version.json` | 公开版本元数据与更新清单源文件 |
| `7z/` | 用于解压的 7-Zip 24.09 运行时 |

## 仓库结构

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

## 开发与构建

直接运行启动器：

```powershell
python nx_toolbox.py
```

构建 Windows 程序：

```powershell
python -m pip install pyinstaller
python build_exe.py
```

构建产物写入已忽略的 `out/` 目录。可执行文件应发布到 GitHub Releases 或项目配置的下载服务，不进入源码树。

## 版本与更新

`version.json` 会正常提交，因为它是公开发布元数据、不含秘密信息，而且构建流程会将它打入程序。发布版本时必须同步：

1. 仓库中的 `version.json`
2. `sd_cleaner/common.py` 中的 `VERSION`
3. 提供给现有客户端的线上更新清单

本仓库采用[语义化版本](https://semver.org/lang/zh-CN/)，重要变更记录在 [CHANGELOG.md](CHANGELOG.md)。

## 参与贡献

欢迎贡献职责明确的新工具、安全性改进或现有模块可靠性修复。提交 Pull Request 前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。每个工具都应能够独立理解，不要让整个启动器与某一个模块强耦合。

## 许可证与第三方内容

nx-toolbox 原创源码和文档采用 [MIT License](LICENSE)。内置第三方内容继续遵循各自条款，详情请查看 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [7z/LICENSE.txt](7z/LICENSE.txt)。

Nintendo、Nintendo Switch、Atmosphère 及相关名称和标志归各自权利人所有。使用者需要自行确认对本软件、固件文件、更新包和内置组件的使用符合适用条款和法律规定。
