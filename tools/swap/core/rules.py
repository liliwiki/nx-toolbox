"""换包规则：决定存储卡上每一项是保留还是清除。

只有三条，按这个顺序判断：
  1. 你手动改过的，听你的
  2. 在黑名单里的，清除
  3. 在白名单里的，保留
其余的一律清除，然后装上新的整合包。

不是可移动存储卡（电脑硬盘、移动硬盘、卡里的子文件夹）时，"其余一律清除"不生效：
只在大气层自己的文件夹里清黑名单和你手动标记的，别的一概不动，整合包里的文件照常覆盖。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SYSTEM_ROOTS = ("emummc", "nintendo")          # 整合包永远不往这里写
SYSTEM_ROOT_NAMES = {"emummc": "emuMMC", "nintendo": "Nintendo"}
IGNORED_ROOTS = {"system volume information", "$recycle.bin", ".trashes", ".spotlight-v100", ".fseventsd"}
LINUX_ROOTS = ("lakka", "ubuntu", "android", "lineage")
MOD_PATCH_DIR = "atmosphere/exefs_patches"
MOD_PATCH_WORDS = ("fps", "graphics", "mod")
SWITCH_ROOTS = ("atmosphere", "bootloader", "switch", "config")   # 不是存储卡时，只有这些（和整合包的顶层文件夹）里能清理
LIST_FILES = ("atmosphere/whitelist.txt", "atmosphere/blacklist.txt", "whitelist.txt", "blacklist.txt")
SD_LIST_DIRS = ("atmosphere", "")

# kind: guard = 白名单，取消保留前要再确认一次；keep = 白名单；clear = 默认清除
GROUPS = [
    {"id": "emu", "kind": "guard", "icon": "game", "name": "正版系统和虚拟系统", "desc": "Nintendo 和 emuMMC 文件夹"},
    {"id": "keys", "kind": "guard", "icon": "key", "name": "密钥和联网证书", "desc": "给真实系统刷机用。没有它，刷机只能刷成被封禁（ban）的机器"},
    {"id": "saves", "kind": "keep", "icon": "save", "name": "存档备份", "desc": "JKSV 等工具导出的存档"},
    {"id": "cheats", "kind": "keep", "icon": "spark", "name": "金手指和 MOD", "desc": "各个游戏的 cheats、romfs、exefs"},
    {"id": "games", "kind": "guard", "icon": "box", "name": "游戏安装包", "desc": "放在卡上的 nsp、nsz、xci、xcz 文件"},
    {"id": "emus", "kind": "keep", "icon": "pad", "name": "模拟器和 ROM", "desc": "RetroArch、PPSSPP、ONS 等和它们的游戏"},
    {"id": "media", "kind": "guard", "icon": "file", "name": "视频和音乐", "desc": "放在卡上的 mp4、mkv、mp3、flac 等"},
    {"id": "amiibo", "kind": "keep", "icon": "tag", "name": "Amiibo 数据", "desc": "emuiibo 里的虚拟 Amiibo"},
    {"id": "linux", "kind": "keep", "icon": "layers", "name": "安卓和 Linux", "desc": "switchroot 和它的启动项"},
    {"id": "misc", "kind": "keep", "icon": "list", "name": "白名单里的其他内容", "desc": "FTP、网盘等工具的配置，和名单文件本身"},
    {"id": "black", "kind": "clear", "icon": "plug", "name": "黑名单", "desc": "会冲突的旧插件、系统模块和主题，不清掉可能开机报错"},
    {"id": "rest", "kind": "clear", "icon": "folder", "name": "其他文件", "desc": "不在白名单里的都会清掉，再装上新的整合包"},
]
NO_SNAPSHOT = ("emu", "games", "media")     # 动辄几十 GB，不进快照
GROUP_DEFAULTS = {g["id"]: g["kind"] != "clear" for g in GROUPS}

# 内置白名单里每条路径属于哪个类别；没列出的归 misc
_WHITE_GROUP = {
    "atmosphere/contents": "cheats", "config/nx-mod-manager": "cheats", "mods2": "cheats",
    "atmosphere/automatic_backups": "keys", "switch/prod.keys": "keys",
    "config/jksv": "saves", "switch/jksv": "saves", "jksv": "saves", "autoback": "saves", "switch/autobackup-manager": "saves",
    "switchroot": "linux", "bootloader/ini": "linux", "emuiibo": "amiibo",
    **{p: "emus" for p in (
        "switch/gbastation.nro", "switch/gbastation", "switch/ppsspp", "switch/noods", "switch/noods.nro",
        "switch/flashnx.nro", "switch/flashnx", "switch/dusklight.nro", "roms", "rom", "onsemu", "sloop+", "retroarch",
        "switch/retroarch_switch.nro", "switch/retroarch_switch", "switch/retroarch.nro", "switch/retroarch",
        "nds", "tico", "emutendo", "flashnx", "gbastation", "dusk", "twilitrealm")},
}
# 按扩展名认出来的用户文件，在卡上哪里都保留（拆分的大游戏是一个叫 xxx.nsp 的文件夹，也算）
_EXT_GROUP = {
    **{e: "games" for e in ("nsp", "nsz", "xci", "xcz")},
    **{e: "emus" for e in (
        "nes", "fds", "sfc", "smc", "gb", "gbc", "gba", "nds", "3ds", "cia", "n64", "z64", "v64", "md", "gen", "smd",
        "sms", "gg", "pce", "ngp", "ngc", "ws", "wsc", "a26", "lnx", "32x", "iso", "cso", "chd", "pbp", "gcm", "rvz",
        "wbfs", "cue")},
    **{e: "media" for e in (
        "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "rmvb", "mp3", "flac", "wav", "aac", "m4a", "ogg",
        "ape", "wma", "opus")},
}

_EXTRA_WHITE = ("bootloader/ini", "switch/retroarch_switch.nro", "switch/retroarch_switch", "switch/retroarch.nro",
                "switch/retroarch")

WHY = {"user": "你的设置", "black": "黑名单", "white": "白名单", "default": "不在白名单", "on": "整组保留", "off": "整组不保留",
       "pc": "不是存储卡，不清理"}


def ext_group(name: str) -> str | None:
    """按扩展名认出来的类别（游戏、ROM、影音），认不出来返回 None。"""
    _, dot, ext = name.rpartition(".")
    return _EXT_GROUP.get(ext.lower()) if dot else None


def norm(path: str) -> str:
    return path.replace("\\", "/").strip().strip("/").lower()


def under(path: str, base: str) -> bool:
    return path == base or path.startswith(base + "/")


def read_list(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return []
    return [line.strip().strip("/\\") for line in lines if line.strip() and not line.lstrip().startswith("#")]


def builtin_lists() -> tuple[list[str], list[str]]:
    white = read_list(DATA_DIR / "whitelist.txt") + list(_EXTRA_WHITE)
    return list(dict.fromkeys(white)), list(dict.fromkeys(read_list(DATA_DIR / "blacklist.txt")))


def sd_lists(sd_root: str) -> tuple[list[str], list[str]]:
    white: list[str] = []
    black: list[str] = []
    for sub in SD_LIST_DIRS:
        white += read_list(Path(sd_root, sub, "whitelist.txt"))
        black += read_list(Path(sd_root, sub, "blacklist.txt"))
    return list(dict.fromkeys(white)), list(dict.fromkeys(black))


@dataclass
class Verdict:
    keep: bool
    why: str
    group: str
    overwritten: bool = False    # 保留下来，但整合包里的同名文件会盖上去


@dataclass
class Node:
    """存储卡上处理方式一致的一项：一个文件，或者整个文件夹。"""
    path: str
    is_dir: bool
    verdict: Verdict
    files: int = 1
    size: int = 0


class RuleSet:
    def __init__(self, groups: dict[str, bool], user: dict[str, str],
                 extra_white: list[tuple[str, str]], extra_black: list[tuple[str, str]],
                 package_files: list[str] | None, card: bool = True) -> None:
        """extra_white / extra_black 是 (路径, 来源)，来源是 "SD 卡" 或 "整合包"。

        card=False 表示不是可移动存储卡，只清大气层文件夹里的黑名单和手动标记的。
        """
        self.card = card
        self.groups = {**GROUP_DEFAULTS, **{k: v for k, v in groups.items() if k in GROUP_DEFAULTS}}
        self.user = {norm(p): v for p, v in user.items() if norm(p)}
        white, black = builtin_lists()
        # (小写路径, 来源, 类别, 原始写法)
        self.white = [(r, "内置", "emu", SYSTEM_ROOT_NAMES[r]) for r in SYSTEM_ROOTS]
        self.white += [(norm(p), "内置", _WHITE_GROUP.get(norm(p), "misc"), p) for p in white]
        self.white += [(norm(p), src, "misc", p) for p, src in extra_white if norm(p)]
        self.white += [(p, "内置", "misc", p) for p in LIST_FILES]
        self.black = [(norm(p), "内置", p) for p in black]
        self.black += [(norm(p), src, p) for p, src in extra_black if norm(p)]
        package = {norm(f) for f in package_files or []}
        self._package = package | _ancestors(package)
        self._switch_roots = set(SWITCH_ROOTS) | {f.split("/", 1)[0] for f in package if "/" in f}
        self._user_inner = _ancestors(self.user)
        self._inner = (_ancestors(p for p, *_ in self.white) | _ancestors(p for p, *_ in self.black)
                       | self._user_inner | {"atmosphere", MOD_PATCH_DIR})

    def _black(self, path: str) -> bool:
        return any(under(path, b) for b, *_ in self.black)

    def group_of(self, path: str) -> str:
        if self._black(path):
            return "black"
        hit = next((w for w in self.white if under(path, w[0])), None)
        if hit:
            return hit[2]
        parts = path.split("/")
        ext = ext_group(parts[-1])
        if ext:
            return ext
        if parts[0].startswith("l4t-") or parts[0] in LINUX_ROOTS:
            return "linux"
        if path.startswith(MOD_PATCH_DIR + "/") and any(w in parts[2] for w in MOD_PATCH_WORDS):
            return "cheats"
        return "rest"

    def user_rule(self, path: str) -> str | None:
        best = max((p for p in self.user if under(path, p)), key=len, default=None)
        return self.user[best] if best else None

    def evaluate(self, path: str) -> Verdict:
        verdict = self._evaluate(path)
        if not self.card and not verdict.keep and (verdict.why == "default" or path.split("/", 1)[0] not in self._switch_roots):
            return Verdict(True, "pc", verdict.group, path in self._package)
        return verdict

    def _evaluate(self, path: str) -> Verdict:
        group = self.group_of(path)
        rule = self.user_rule(path)
        if rule:
            # 手动保留的，连整合包的同名文件也不让盖
            return Verdict(rule == "keep", "user", group)
        on = self.groups[group]
        if group == "black":
            return Verdict(True, "on", group, path in self._package) if on else Verdict(False, "black", group)
        if group == "rest":
            return Verdict(True, "on", group, path in self._package) if on else Verdict(False, "default", group)
        return Verdict(True, "white", group, path in self._package) if on else Verdict(False, "off", group)

    def needs_descent(self, path: str) -> bool:
        """文件夹里面有没有处理方式不一样的东西；没有就整个当一项。"""
        if path in self._user_inner:
            return True
        if self._black(path):
            return False
        return path in self._inner

    def may_delete(self, path: str) -> bool:
        """最后一道关：不是存储卡时，大气层文件夹以外的东西绝不删。"""
        return self.card or norm(path).split("/", 1)[0] in self._switch_roots

    def will_install(self, package_path: str) -> bool:
        path = norm(package_path)
        return path.split("/", 1)[0] not in SYSTEM_ROOTS and self.user_rule(path) != "keep"

    def listing(self) -> list[dict]:
        """给"规则"表格用。整合包和存储卡自带的名单常和内置的重复，同一条只留最先出现的。"""
        rows = [{"act": "keep" if v == "keep" else "del", "path": p, "src": "你的设置"} for p, v in self.user.items()]
        rows += [{"act": "del", "path": raw, "src": src} for _, src, raw in self.black]
        rows += [{"act": "keep", "path": raw, "src": src} for _, src, group, raw in self.white if self.groups[group]]
        return list({(r["act"], norm(r["path"])): r for r in reversed(rows)}.values())[::-1]


def _ancestors(paths) -> set[str]:
    result: set[str] = set()
    for path in paths:
        parts = path.split("/")
        for i in range(1, len(parts)):
            result.add("/".join(parts[:i]))
    return result


def _measure(path: str) -> tuple[int, int]:
    files = size = 0
    for root, _, names in os.walk(path):
        for name in names:
            files += 1
            try:
                size += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return files, size


def _holds_ext(folder: str) -> bool:
    """文件夹里有没有按扩展名认得出的游戏、影音。

    不管那一类开没开都往里走：开着就留下，关着也要单独算进那一类，免得几十 GB 的游戏被当成普通文件夹存进快照。
    """
    for _, dirs, names in os.walk(folder):
        if any(ext_group(n) for n in dirs + names):
            return True
    return False


def scan(sd_root: str, rules: RuleSet) -> list[Node]:
    """走一遍存储卡。处理方式一致的文件夹不往里走，所以大的存档、游戏目录不会被遍历。"""
    nodes: list[Node] = []

    def walk(folder: str, rel: str) -> None:
        try:
            entries = sorted(os.scandir(folder), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
        except OSError:
            return
        for entry in entries:
            if not rel and entry.name.lower() in IGNORED_ROOTS:
                continue
            path = f"{rel}/{entry.name}" if rel else entry.name
            key = path.lower()
            is_dir = entry.is_dir(follow_symlinks=False)
            if is_dir and rules.needs_descent(key):
                walk(entry.path, path)
                continue
            verdict = rules.evaluate(key)
            if is_dir and not verdict.keep and verdict.why == "default" and _holds_ext(entry.path):
                # 一个普通文件夹里放了游戏、视频：往里走，只清别的，游戏留下
                walk(entry.path, path)
                continue
            node = Node(path, is_dir, verdict)
            if is_dir and not verdict.keep:
                node.files, node.size = _measure(entry.path)
            elif not is_dir and (not verdict.keep or verdict.overwritten):
                try:
                    node.size = entry.stat().st_size
                except OSError:
                    pass
            nodes.append(node)

    walk(sd_root, "")
    return nodes
