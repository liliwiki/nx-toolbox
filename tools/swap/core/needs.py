"""白名单里的东西留下来有没有用。

白名单只说了"这个路径要留"，但很多时候留下来并没有意义：
  - 空的文件夹
  - 插件和它的配置，可卡上根本没有对应的存档、游戏（JKSV 却没有存档，模拟器却没有 ROM）
  - 附属的东西，主人却不在了（没有 switchroot 的安卓启动项，插件不在了的配置）
  - atmosphere/contents 里不是金手指和 MOD 的旧系统模块

这些就当它不在白名单里，按"其余一律清除"处理。
虚拟系统、密钥、游戏安装包、影音这几组不在这里判断，留不留由用户自己决定。
"""

from __future__ import annotations

import os
import re
import struct

JUNK = {".ds_store", "thumbs.db", "desktop.ini"}
SKIP_ROOTS = ("emummc", "nintendo", "atmosphere", "bootloader", "config")   # 找游戏资源时不进这些文件夹
CONTENTS = "atmosphere/contents"
MOD_DIRS = ("cheats", "romfs", "exefs")
LINUX_INI = re.compile(r"l4t|switchroot|android|lineage|ubuntu|lakka|fedora", re.I)
EMU_ALL = "*"

# 程序和它的附属物：卡上有对应的游戏资源（扩展名 exts 的文件，或者 dirs 文件夹里有东西）才留
OWNERS = [
    {"paths": ("switch/jksv", "switch/jksv.nro", "config/jksv"), "dirs": ("jksv",)},
    {"paths": ("switch/autobackup-manager", "switch/.overlays/ftpautoback.ovl"), "dirs": ("autoback",)},
    {"paths": ("config/nx-mod-manager",), "dirs": ("mods2",)},
    {"paths": ("switch/gbastation.nro", "switch/gbastation", "gbastation"), "exts": ("gba", "gb", "gbc")},
    {"paths": ("switch/ppsspp",), "exts": ("iso", "cso", "pbp", "chd")},
    {"paths": ("switch/noods", "switch/noods.nro"), "exts": ("nds",), "dirs": ("nds",)},
    {"paths": ("switch/flashnx.nro", "switch/flashnx", "flashnx"), "exts": ("swf",)},
    {"paths": ("switch/dusklight.nro", "dusk", "twilitrealm"), "exts": ("iso", "gcm", "rvz", "ciso")},
    {"paths": ("retroarch", "switch/retroarch_switch.nro", "switch/retroarch_switch", "switch/retroarch.nro",
               "switch/retroarch"), "exts": EMU_ALL, "dirs": ("roms", "rom")},
]
# 插件的配置：插件还在（新整合包里有，或者卡上的会留下）才留
CONFIGS = {
    "config/ftpsrv": ("ftpsrv", "sphaira", "ftp"),
    # config/cloud-file、config/cloud-quark 不知道是哪个插件的，先按"里面有东西就留"处理：误删会丢网盘登录
}
# 这些组不判断，用户自己决定
FREE_GROUPS = ("emu", "keys", "games", "media")

REASONS = {
    "empty": "是空的",
    "unused": "卡上没有用得到它的游戏或存档",
    "orphan": "用它的插件不在了",
    "linux": "卡上没有安卓和 Linux",
    "notlinux": "不是安卓和 Linux 的启动项",
    "module": "旧的系统模块，新整合包会重装",
    "nomod": "里面没有金手指和 MOD",
    "front": "插件前端，不是游戏",
    "plugmedia": "插件自带的音乐、视频",
}
_OWNER_OF = {p: o for o in OWNERS for p in o["paths"]}


class Needs:
    def __init__(self, root: str, package: set[str], emu_exts: set[str], keeps) -> None:
        """keeps(path) 告诉我们卡上的某一项换包后还在不在，用来判断插件配置有没有主人。"""
        self.root = root
        self.package = package
        self.emu_exts = emu_exts
        self.keeps = keeps
        self._files: dict[str, bool] = {}
        self._reason: dict[tuple[str, str], str | None] = {}
        self._resources: set[str] | None = None

    # ---------- 卡上的情况 ----------

    def _full(self, path: str) -> str:
        return os.path.join(self.root, *path.split("/"))

    def has_files(self, path: str) -> bool:
        """里面有没有真正的文件（系统自动生成的 .DS_Store 之类不算）。是文件就看它本身。"""
        if path not in self._files:
            full = self._full(path)
            if os.path.isfile(full):
                found = True
            else:
                found = any(n.lower() not in JUNK and not n.startswith("._")
                            for _, _, names in os.walk(full) for n in names)
            self._files[path] = found
        return self._files[path]

    def resources(self) -> set[str]:
        """卡上能找到的游戏资源的扩展名。系统和大气层自己的文件夹里不找。"""
        if self._resources is None:
            found: set[str] = set()

            def note(name: str) -> None:
                _, dot, ext = name.rpartition(".")
                if dot and ext.lower() in self.emu_exts:
                    found.add(ext.lower())

            try:
                tops = [e for e in os.scandir(self.root) if e.name.lower() not in SKIP_ROOTS]
            except OSError:
                tops = []
            for top in tops:
                note(top.name)
                if top.is_dir(follow_symlinks=False):
                    for _, dirs, files in os.walk(top.path):
                        for name in dirs + files:
                            note(name)
            self._resources = found
        return self._resources

    # ---------- 判断 ----------

    def reason(self, entry: str, group: str) -> str | None:
        """白名单里的 entry 留下来没用时返回原因（REASONS 的键），有用返回 None。

        entry 是命中的白名单路径；atmosphere/contents 下面按每个游戏的文件夹判断，bootloader/ini 按每个文件判断。
        """
        if group in FREE_GROUPS:
            return None
        key = (entry, group)
        if key not in self._reason:
            self._reason[key] = self._judge(entry)
        return self._reason[key]

    def _judge(self, entry: str) -> str | None:
        if not os.path.exists(self._full(entry)):
            return None
        if entry.startswith(CONTENTS + "/"):
            return self._content(entry)
        if entry.startswith("bootloader/ini/"):
            return self._linux_ini(entry)
        owner = _OWNER_OF.get(entry)
        if owner:
            return None if self._owner_needed(owner) else "unused"
        plugins = CONFIGS.get(entry)
        if plugins:
            return None if self._plugin_present(plugins) else "orphan"
        return None if self.has_files(entry) else "empty"

    def _owner_needed(self, owner: dict) -> bool:
        if any(self.has_files(d) for d in owner.get("dirs", ()) if os.path.exists(self._full(d))):
            return True
        exts = owner.get("exts", ())
        found = self.resources()
        return bool(found) if exts == EMU_ALL else any(e in found for e in exts)

    def _plugin_present(self, words: tuple[str, ...]) -> bool:
        def named(path: str) -> bool:
            return any(w in path for w in words)

        if any(named(p) for p in self.package if p.startswith("switch/")):
            return True
        for sub in ("switch", "switch/.overlays"):
            try:
                entries = list(os.scandir(self._full(sub)))
            except OSError:
                continue
            for e in entries:
                path = f"{sub}/{e.name.lower()}"
                if named(e.name.lower()) and self.keeps(path):
                    return True
        return False

    def official_nsp(self, path: str) -> bool:
        """nsp 里带不带门票（.tik）。带的是正版游戏、更新或 DLC；不带的基本是插件前端。

        只读文件开头的目录，不用密钥。拆分的大游戏（叫 xxx.nsp 的文件夹）、读不出来的都当成游戏，宁可多留。
        """
        full = self._full(path)
        if not os.path.isfile(full):
            return True
        try:
            with open(full, "rb") as f:
                head = f.read(16)
                if len(head) < 16 or head[:4] != b"PFS0":
                    return True
                count, table = struct.unpack_from("<II", head, 4)
                if not count or count > 4096 or table > 1 << 20:
                    return True
                f.seek(16 + count * 24)
                names = f.read(table)
        except OSError:
            return True
        return any(n.lower().endswith(b".tik") for n in names.split(bytes(1)))

    def _linux_ini(self, entry: str) -> str | None:
        if not self.has_files("switchroot"):
            return "linux"
        try:
            with open(self._full(entry), encoding="utf-8", errors="replace") as f:
                text = f.read(65536)
        except OSError:
            return None     # 是文件夹或读不了，不乱删
        return None if LINUX_INI.search(text) else "notlinux"

    def _content(self, entry: str) -> str | None:
        """atmosphere/contents/<编号>：游戏的金手指和 MOD 留，系统模块和空的清。"""
        full = self._full(entry)
        if not os.path.isdir(full):
            return "nomod"
        if os.path.exists(os.path.join(full, "exefs.nsp")) or os.path.exists(os.path.join(full, "flags", "boot2.flag")):
            return "module"
        if any(self.has_files(f"{entry}/{d}") for d in MOD_DIRS if os.path.isdir(os.path.join(full, d))):
            return None
        return "nomod"
