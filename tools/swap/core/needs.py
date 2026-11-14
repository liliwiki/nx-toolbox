"""白名单里的东西留下来有没有用。

白名单只说了"这个路径要留"，但"里面有文件"不等于"里面有用户的数据"。这里统一按"数据档案"判断：

  1. 噪音一律不算：日志（*.log、log.txt、xxx_log.txt）、0 字节的文件、系统垃圾（.DS_Store 等），
     以及 log、logs、cache、temp、回收站这类文件夹里的所有东西
  2. 每个白名单项有自己的档案（PROFILES），说明什么才算它的数据。档案按各工具的源码核对过目录结构：
       - 要在第几层：JKSV 根目录的 config.json 不算，JKSV/<游戏>/ 下面的才是存档
       - 只看哪几个子文件夹：emuiibo 只看 amiibo/（miis/ 每次开机都会重新导出）
       - 只认哪些文件：ROM 文件夹认游戏和存档，AutoBack 认 zip，ONS 认 nscript.dat、*.nsa
       - 哪些文件不算：NX-Mod-Manager 的 gameInfo.json、modInfo.json 这类索引
     没有档案的按"去掉噪音后还有文件"判断
     JKSV 的存档文件夹可以在设置里改（config/JKSV/JKSV.json 的 WorkingDirectory），改过的也一并保护
  3. 程序和它的配置跟着数据走（OWNERS）：卡上没有它能用的存档、游戏，程序和配置一起清
  4. 附属的东西跟着主人走：插件不在了的配置、没有 switchroot 的安卓启动项
  5. atmosphere/contents 按每个游戏看：只留金手指和 MOD，旧系统模块清掉

判断为没用的就当它不在白名单里，按"其余一律清除"处理，界面上会写原因（REASONS）。
虚拟系统、密钥、游戏安装包、影音这几组不在这里判断，留不留由用户自己决定。
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import struct
from dataclasses import dataclass

# ---------- 噪音：在哪都不算数据 ----------
JUNK = {"thumbs.db", "desktop.ini"}
NOISE_DIRS = {"log", "logs", "cache", "caches", "temp", "tmp", "_trash_", "trash", ".trash", ".thumbnails", "debug"}
# 日志、临时文件；点开头的隐藏文件（.DS_Store、._xxx、Dusklight 的 .guard）是工具自己的标记
NOISE_FILE = re.compile(r"^\.|(\.log|\.tmp)$|(^|[_\-. ])logs?([_\-. ]\w*)?\.txt$|^std(out|err)\.txt$")
# 模拟器存档、即时存档：RetroArch .srm/.state/.state.auto，NooDS .sav/.sv2/.noo，GBAStation .ss0，
# PPSSPP .ppst，FlashNX .sol（Flash 的 SharedObject）
SAVE_FILE = re.compile(r"\.(sav|sv\d|srm|brm|dsv|eep|fla|sra|mcr|mcd|sol|ppst|noo|no\d|st\d|ss\d|state\d*(\.auto)?)$")
ARCHIVES = ("zip", "7z", "rar")
ROM_EXTRA = ("bin", "m3u", "cci", "cxi", "3dsx", "wad", "gcz", "ciso", "swf", "p8")   # 只在 ROM 文件夹里才当游戏

SKIP_ROOTS = ("emummc", "nintendo", "atmosphere", "bootloader", "config")   # 找游戏资源时不进这些文件夹
CONTENTS = "atmosphere/contents"
MOD_DIRS = ("cheats", "romfs", "exefs")
LINUX_INI = re.compile(r"l4t|switchroot|android|lineage|ubuntu|lakka|fedora", re.I)
EMU_ALL = "*"
ROMS = "@rom"     # 档案里代表"模拟器游戏的扩展名和压缩包"


@dataclass(frozen=True)
class Data:
    """一个文件夹里什么才算真正的数据（噪音已经去掉）。

    why     没有数据时给用户看的原因（REASONS 的键）
    depth   文件至少在第几层（1 = 直接放在里面就算，2 = 要在子文件夹里）
    exts    只认这些扩展名，空的话什么都算；ROMS 代表模拟器游戏
    names   也认这些文件名
    saves   模拟器存档也算
    only    只看这几个子文件夹（相对路径，小写），空的话整个都看
    skip    这些名字的子文件夹不看（工具自带的资源、缓存）
    ignore  这些文件不算（小写，可以用 * 通配）
    """
    why: str = "empty"
    depth: int = 1
    exts: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    saves: bool = False
    only: tuple[str, ...] = ()
    skip: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()


PLAIN = Data()
SAVES_ONLY = Data(exts=("\0",), saves=True)
_ROM_DIR = Data("norom", exts=(ROMS,), saves=True)
# JKSV（J-D-K/JKSV）：工作目录默认 sdmc:/JKSV，备份在 <工作目录>/<游戏>/<备份名>（文件夹或 .zip）。
#   _TRASH_ 是删掉的备份；svi 是导出的存档信息，单独没用；saves 是等着导入的存档，算数据
JKSV_DATA = Data("nosave", depth=2, skip=("svi",))
PROFILES: dict[str, Data] = {
    "jksv": JKSV_DATA,
    # FtpAutoBack（BIGBIGSUI/FtpAutoBack）：AutoBack/<用户名>/<游戏>/<游戏>_<用户名>_<时间>_<序号>.zip，根目录是 backuplog.txt
    "autoback": Data("nosave", depth=3, exts=("zip",)),
    # NX-Mod-Manager（TOM-BadEN）：mods2/<游戏>/<appid>/<MOD>/，根目录 gameInfo.json 是索引，!temp_mods/ 是等着添加的 MOD
    "mods2": Data("nomod", depth=2, ignore=("gameinfo.json", "modinfo.json", "modfilerefcount.json", "*installmap.json")),
    # emuiibo（XorTroll）：虚拟 Amiibo 在 emuiibo/amiibo/<名字>/；miis/ 每次开机重新导出，flags/、overlay/ 是设置
    "emuiibo": Data("noamiibo", only=("amiibo",)),
    # ONScripter（onsyuri 等）：sdmc:/onsemu/<游戏>/ 里有 0.txt/00.txt/nscript.dat 和 arc*.nsa，存档 save*.dat
    "onsemu": Data("norom", exts=("nsa", "sar", "ns2", "ns3"), ignore=("default.ttf", "*.nro"),
                   names=("nscript.dat", "nscript.___", "nscr_sec.dat", "onscript.nt2", "onscript.nt3", "0.txt", "00.txt")),
    "roms": _ROM_DIR, "rom": _ROM_DIR, "nds": _ROM_DIR,
    # tico（ticohq，闭源）：sdmc:/tico 里是配置、BIOS、模块和 roms/<主机>/，认游戏和存档
    "tico": _ROM_DIR,
    # NXThemes Installer（exelix11/SwitchThemeInjector）：sdmc:/themes/ 下任意子文件夹里的主题；
    #   systemData/（从主机导出的原始文件）、systemPatches/（自动生成的补丁）、providers.json 是它自己的
    "themes": Data("notheme", skip=("systemdata", "systempatches"),
                   exts=("nxtheme", "szs", "zip", "jpg", "jpeg", "png", "bmp", "ttf")),
    # Dusklight 的 Switch 版：sdmc:/switch/dusklight/ 里是光盘镜像、存档、设置和 MOD；cache/（含日志）、benchmarks/ 不算
    "switch/dusklight": Data("nosave", skip=("benchmarks",), ignore=("*.nro",)),
}
PROFILES["theme"] = PROFILES["themes"]       # 有的整合包写成单数的 Theme

# 程序和它的附属物：卡上有它用得到的数据才留 ——
#   dirs 里的数据文件夹按档案有数据，或者卡上有扩展名 exts 的游戏，或者（模拟器）自己文件夹里有存档
OWNERS = [
    {"paths": ("switch/jksv", "switch/jksv.nro", "config/jksv"), "dirs": ("jksv",)},
    # FtpAutoBack 的配置和 ftpsrv 共用 config/ftpsrv/config.ini
    {"paths": ("switch/autobackup-manager", "switch/.overlays/ftpautoback.ovl"), "dirs": ("autoback",)},
    {"paths": ("config/nx-mod-manager",), "dirs": ("mods2",)},
    # GBAStation（beiklive）现在是多机种前端（FC 到 3DS、PS1、DC、街机、PSP），数据在 GBAStation/ 的这些子文件夹
    {"paths": ("switch/gbastation.nro", "switch/gbastation", "gbastation"), "exts": EMU_ALL,
     "dirs": ("gbastation/roms", "gbastation/saves", "gbastation/duckstation/memcards",
              "gbastation/duckstation/savestates", "gbastation/backup", "gbastation/export")},
    # PPSSPP：switch/ppsspp 是记忆棒，PSP/SAVEDATA 存档、PSP/PPSSPP_STATE 即时存档、PSP/GAME 游戏
    {"paths": ("switch/ppsspp",), "exts": ("iso", "cso", "pbp", "chd"),
     "dirs": ("switch/ppsspp/psp/savedata", "switch/ppsspp/psp/ppsspp_state", "switch/ppsspp/psp/game")},
    # NooDS：存档默认在 ROM 旁边，也可以放 switch/noods/saves、states
    {"paths": ("switch/noods", "switch/noods.nro"), "exts": ("nds",),
     "dirs": ("nds", "switch/noods/saves", "switch/noods/states")},
    # FlashNX：游戏和存档（.sol）都在 sdmc:/flashnx
    {"paths": ("switch/flashnx.nro", "switch/flashnx", "flashnx"), "exts": ("swf",)},
    {"paths": ("switch/dusklight.nro", "dusk", "twilitrealm"), "exts": ("iso", "gcm", "rvz", "ciso"),
     "dirs": ("switch/dusklight",)},
    # RetroArch：存档默认在 retroarch/cores/savefiles、savestates
    {"paths": ("retroarch", "switch/retroarch_switch.nro", "switch/retroarch_switch", "switch/retroarch.nro",
               "switch/retroarch"), "exts": EMU_ALL,
     "dirs": ("roms", "rom", "retroarch/cores/savefiles", "retroarch/cores/savestates")},
]
# 插件的配置：插件还在（新整合包里有，或者卡上的会留下），而且里面有数据才留
CONFIGS = {
    "config/ftpsrv": ("ftpsrv", "sphaira", "ftp"),
    # config/cloud-file、config/cloud-quark 找不到源码，按通用档案判断：误删会丢网盘登录
}
JKSV_CONFIG = "config/jksv/jksv.json"
# 大气层读 sdmc:/emummc/emummc.ini 的 [emummc]：path 是文件版虚拟系统的位置（比如从 SX OS 迁来的 sxos/emunand），
# nintendo_path 是虚拟系统用的 Nintendo 文件夹（SX OS 迁来的叫 Emutendo）。老版本的键名前面带 emummc_
EMUMMC_INI = "emummc/emummc.ini"
EMUMMC_KEYS = ("path", "nintendo_path", "emummc_path", "emummc_nintendo_path")
_SYSTEM_TOPS = {"", "atmosphere", "bootloader", "switch", "config", "emummc", "nintendo"}
# 这些组不判断，用户自己决定
FREE_GROUPS = ("emu", "keys", "games", "media")

REASONS = {
    "empty": "是空的，或者只有日志和空文件",
    "nosave": "没有存档，只有日志、配置或空文件夹",
    "norom": "里面没有游戏和存档",
    "noamiibo": "里面没有 Amiibo",
    "notheme": "里面没有主题，只有从主机导出的文件",
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


def noise(name: str) -> bool:
    """日志、系统垃圾这类文件，在哪都不算数据。"""
    low = name.lower()
    return low in JUNK or bool(NOISE_FILE.search(low))


def _sd_path(value: str) -> str | None:
    """设置里写的 SD 卡路径（sdmc:/xxx、/xxx、xxx）换成卡上的相对路径（小写）。指到系统文件夹或卡根目录的不算。"""
    path = value.split(":", 1)[-1].replace("\\", "/").strip().strip("/").lower()
    if path in _SYSTEM_TOPS or ".." in path.split("/"):
        return None
    return path


def emummc_dirs(root: str) -> list[str]:
    """emummc.ini 里虚拟系统用到的文件夹（不在 emuMMC、Nintendo 里的那些也要保护）。"""
    try:
        with open(os.path.join(root, *EMUMMC_INI.split("/")), encoding="utf-8", errors="replace") as f:
            lines = f.read(65536).splitlines()
    except OSError:
        return []
    found: list[str] = []
    section = ""
    for line in lines:
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
        elif section == "emummc" and "=" in line:
            key, value = (s.strip() for s in line.split("=", 1))
            path = _sd_path(value) if key.lower() in EMUMMC_KEYS else None
            if path and path not in found:
                found.append(path)
    return found


def jksv_workdir(root: str) -> str | None:
    """JKSV 设置里改过的存档文件夹（卡上的相对路径，小写）。没改过、读不出来、指到系统文件夹的返回 None。"""
    try:
        with open(os.path.join(root, *JKSV_CONFIG.split("/")), encoding="utf-8") as f:
            value = json.load(f).get("WorkingDirectory")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(value, str):
        return None
    path = _sd_path(value)
    return None if path == "jksv" else path


class Needs:
    def __init__(self, root: str, package: set[str], emu_exts: set[str], keeps) -> None:
        """keeps(path) 告诉我们卡上的某一项换包后还在不在，用来判断插件配置有没有主人。"""
        self.root = root
        self.package = package
        self.emu_exts = emu_exts
        self.keeps = keeps
        self._data: dict[tuple[str, Data], bool] = {}
        self._reason: dict[tuple[str, str], str | None] = {}
        self._resources: set[str] | None = None
        self.jksv_dir = jksv_workdir(root)       # JKSV 改过的存档文件夹，rules 会把它加进白名单

    # ---------- 卡上的情况 ----------

    def _full(self, path: str) -> str:
        return os.path.join(self.root, *path.split("/"))

    def profile(self, path: str) -> Data:
        if path == self.jksv_dir:
            return JKSV_DATA
        return PROFILES.get(path, PLAIN)

    def has_data(self, path: str, spec: Data | None = None) -> bool:
        """path 里有没有按档案算数的数据（不给档案就按路径查 PROFILES）。是文件就看它本身。"""
        spec = spec or self.profile(path)
        key = (path, spec)
        if key not in self._data:
            self._data[key] = self._find(self._full(path), spec)
        return self._data[key]

    def _find(self, full: str, spec: Data) -> bool:
        if os.path.isfile(full):
            return self._counts(os.path.basename(full), full, spec)
        starts = [os.path.join(full, *o.split("/")) for o in spec.only] if spec.only else [full]
        skip = NOISE_DIRS | set(spec.skip)
        for start in starts:
            for here, dirs, names in os.walk(start):
                dirs[:] = [d for d in dirs if d.lower() not in skip and not d.startswith(".")]   # 隐藏文件夹是工具的标记
                rel = os.path.relpath(here, full)
                depth = 1 if rel == "." else rel.count(os.sep) + 2
                if depth >= spec.depth and any(self._counts(n, os.path.join(here, n), spec) for n in names):
                    return True
        return False

    def _counts(self, name: str, full: str, spec: Data) -> bool:
        low = name.lower()
        if noise(low) or any(fnmatch.fnmatchcase(low, p) for p in spec.ignore):
            return False
        try:
            if os.path.getsize(full) == 0:
                return False
        except OSError:
            return False
        if spec.saves and SAVE_FILE.search(low):
            return True
        if not spec.exts and not spec.names:
            return True
        if low in spec.names:
            return True
        _, dot, ext = low.rpartition(".")
        if not dot:
            return False
        allowed = set(spec.exts)
        if ROMS in allowed:
            allowed |= self.emu_exts | set(ARCHIVES) | set(ROM_EXTRA)
        return ext in allowed

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
        if plugins and not self._plugin_present(plugins):
            return "orphan"
        spec = self.profile(entry)
        return None if self.has_data(entry, spec) else spec.why

    def _owner_needed(self, owner: dict) -> bool:
        dirs = list(owner.get("dirs", ()))
        if "jksv" in dirs and self.jksv_dir:
            dirs.append(self.jksv_dir)
        if any(self.has_data(d) for d in dirs if os.path.exists(self._full(d))):
            return True
        exts = owner.get("exts", ())
        if not exts:
            return False
        found = self.resources()
        if found if exts == EMU_ALL else any(e in found for e in exts):
            return True
        # 模拟器自己的文件夹里有存档（游戏删了，存档还想留着）
        return any(self.has_data(p, SAVES_ONLY) for p in owner["paths"] if os.path.isdir(self._full(p)))

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
        if not self.has_data("switchroot"):
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
        if any(self.has_data(f"{entry}/{d}") for d in MOD_DIRS if os.path.isdir(os.path.join(full, d))):
            return None
        return "nomod"
