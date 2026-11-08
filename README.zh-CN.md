# nx-toolbox

[![Windows 工具箱](https://img.shields.io/badge/Windows-Toolbox-111827)](https://github.com/Lili679280/nx-toolbox)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB)](https://www.python.org/)
[![许可证：MIT](https://img.shields.io/badge/许可证-MIT-green.svg)](LICENSE)

[English](README.md) | **简体中文**

Nintendo Switch 维护工具。一个启动器加上若干独立的工具，目前可用的是换包工具。

> 这是 2.0 的开发分支，当前发布的是预览版。1.0 的源码在 [main 分支](https://github.com/Lili679280/nx-toolbox/tree/main)。
>
> 本仓库是非官方社区项目，与 Nintendo 没有隶属、赞助或认可关系。

## 下载

在 [Releases](https://github.com/Lili679280/nx-toolbox/releases) 页面下载 `NXToolbox-<版本>.exe`，双击安装。可以选安装位置和快捷方式，只安装给当前用户，不需要管理员权限；要卸载的话，再运行一次安装程序，或者在系统设置的「已安装的应用」里卸载。

需要 Windows 10 或 11，以及 WebView2 运行时（Windows 11 已内置）。程序没有代码签名，运行前请核对 Release 页面公布的 SHA-256。

## 换包工具

把存储卡上的大气层整合包换成新版。

规则只有三条，按顺序判断：

1. 你手动勾选或取消的，按你的来
2. 在黑名单里的，清除
3. 在白名单里的，保留

其余的一律清除，然后装上新的整合包。

- 点更新之前就能看到会改动哪些文件，按文件夹一层层查看
- 在线整合包只读取文件清单，不用先下载整个包
- 更新前创建快照，清掉和覆盖的文件可以恢复
- 写入后逐个文件校验
- 读取整合包和存储卡自带的 `whitelist.txt`、`blacklist.txt`
- 固件下载与复制

内置名单在 [tools/swap/data](tools/swap/data)。

## 安全边界

换包工具会删除和覆盖存储卡上的文件。请确认选对了存储卡，并在更新前检查列表。快照可以降低风险，但不能替代完整备份；「正版系统和虚拟系统」体积太大，不会进入快照。

不要在公开 Issue 中上传主机密钥、联网证书、个人存档或设备标识。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 结构

```text
host.py            宿主：同一个程序按参数启动成启动器或某个工具
bootstrap.py       单文件安装程序
setup/             安装和卸载界面
nxhost/            启动器和工具共用的部分（设置、日志、更新、查找第三方程序）
launcher/          启动器页面
ui/                共用的样式和脚本
tools/swap/        换包工具（core/ 是后端，index.html 是页面）
build.py           打包并自测
```

每个工具是一个文件夹，带一份 `tool.json`。启动器只读这份描述，用独立进程启动工具；需要管理员权限的工具只在启动时单独提权。

界面是网页，跑在 [pywebview](https://pywebview.flowrl.com/) 和系统的 WebView2 里；逻辑是 Python。

## 开发

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python host.py                 # 启动器
.venv\Scripts\python host.py run swap        # 换包工具
.venv\Scripts\python host.py dev swap        # 在浏览器里调试页面
.venv\Scripts\python build.py                # 打包到 dist/ 和 release/
```

调试时可以把普通文件夹当成存储卡：设置环境变量 `NXTOOLBOX_FAKE_SD=<文件夹>`。

## 版本

版本号写在 `app.json`。采用[语义化版本](https://semver.org/lang/zh-CN/)，变更记录在 [CHANGELOG.md](CHANGELOG.md)。

## 代码签名政策

Windows 安装程序由 GitHub Actions 上的 [Build 工作流](.github/workflows/build.yml)从本仓库打了标签的源码打包。代码签名已向 SignPath Foundation 开源计划申请，通过前发布文件没有签名。通过后：Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by [SignPath Foundation](https://signpath.org)（由 SignPath.io 免费提供代码签名，证书由 SignPath Foundation 签发）。

- 提交与审核：[liliwiki](https://github.com/liliwiki)
- 批准签名：[liliwiki](https://github.com/liliwiki)
- 只签本仓库构建出的程序。第三方文件保留原作者的签名或不签名（见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)）。

### 隐私

本程序不收集、不上传个人数据，只在这些功能里联网：

- 检查更新：读取 `https://download.liliwiki.com/toolbox/version.json`，你选择更新时从同一网站下载安装程序。
- 在线整合包：你选择在线整合包或固件时，从 `https://download.liliwiki.com/` 或你填写的地址读取文件清单并下载。
- 缺少 WebView2 时，经你同意后用浏览器打开微软的下载地址。

## 许可证与第三方内容

原创源码和文档采用 [MIT License](LICENSE)。第三方内容遵循各自条款，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

Nintendo、Nintendo Switch、Atmosphère 及相关名称和标志归各自权利人所有。
