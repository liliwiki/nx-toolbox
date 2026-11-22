"""atmosphere/exefs_patches 里的东西是游戏 MOD，还是整合包带的系统补丁。

大气层的 exefs_patches/<补丁集名字>/<Build ID>.ips（见大气层文档 docs/components/modules/loader.md）：
文件名是要打补丁的那个程序（NSO）的 Build ID，十六进制，末尾的 0 可以省掉；内容是 IPS 或 IPS32 格式
（文件头 PATCH / IPS32）。外层文件夹名随便起，MOD 一般是"游戏名 + 60fps"之类，所以不能靠名字判断。
有的 MOD 还附带 .pchtxt（文本格式的补丁，大气层不读，但 Build ID 写在里面的 @nsobid- 那一行，
开头的注释一般写着游戏名）。所以看的是里面的补丁打给谁：

  - 系统补丁（签名补丁 es_patches、联网检测 nfim_ctest 这类）打给系统模块，整合包每次都会带，换包会重装
  - 游戏 MOD（画质、帧率、去模糊）打给游戏，是用户自己装的，要留

怎么知道一个 Build ID 是系统模块的：
  1. 新整合包里 exefs_patches 下所有补丁的 Build ID —— 整合包带的就是系统补丁
  2. 以前读过的整合包里见过的（存在设置里，换过几次包就认得全了）
  3. 实在认不出时，按大家通用的系统补丁文件夹名兜底（es_patches 永远只装签名补丁）
游戏和系统模块不会共用 Build ID，所以文件夹里只要有一个是系统的，整个文件夹就是系统补丁。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# exefs_patches 打给程序本体（NSO），nro_patches 打给游戏运行时加载的 NRO 库，格式一样（见大气层文档 ro.md）。
# kip_patches 打给系统核心模块，只有整合包会带，不在这里
PATCH_DIRS = ("atmosphere/exefs_patches", "atmosphere/nro_patches")
IPS_FILE = re.compile(r"^([0-9a-f]{8,64})\.ips$", re.I)
NSOBID = re.compile(r"@nsobid-([0-9a-f]{8,64})", re.I)
IPS_MAGIC = (b"PATCH", b"IPS32")   # 大气层认的两种格式
KEY_LEN = 16      # Build ID 有的写 40 位、有的补零写到 64 位，比前 16 位就够分清了
# 第 3 条兜底用：这些名字的文件夹从来只装系统补丁
SYSTEM_PATCH_DIRS = {"es_patches", "es_patch", "nfim_ctest", "nifm_ctest", "nim_ctest", "am_no_copyright",
                     "bootlogo", "boot_logo", "sigpatches"}
TITLE_LINE = re.compile(r"^\s*(?:#|//|@title\s)\s*(.+?)\s*$", re.I)
MAX_LEARNED = 5000


def key(build_id: str) -> str:
    return build_id.lower()[:KEY_LEN]


def package_ids(files) -> set[str]:
    """整合包文件清单里 exefs_patches 下所有补丁的 Build ID。"""
    found = set()
    for path in files or ():
        parts = path.replace("\\", "/").strip("/").split("/")
        if len(parts) >= 4 and "/".join(parts[:2]).lower() in PATCH_DIRS:
            m = IPS_FILE.match(parts[-1])
            if m:
                found.add(key(m.group(1)))
    return found


@dataclass
class PatchFolder:
    ids: set[str] = field(default_factory=set)      # 里面补丁的 Build ID（前 16 位）
    files: int = 0                                  # 补丁文件个数
    title: str = ""                                 # pchtxt 里写的游戏名


def read_folder(full: str) -> PatchFolder:
    """读一个 exefs_patches/<名字> 文件夹：有哪些补丁、打给哪个游戏。0 字节的不算。"""
    info = PatchFolder()
    for here, _, names in os.walk(full):
        for name in names:
            path = os.path.join(here, name)
            try:
                if os.path.getsize(path) == 0:
                    continue
            except OSError:
                continue
            m = IPS_FILE.match(name)
            if m and _is_ips(path):
                info.ids.add(key(m.group(1)))
                info.files += 1
            elif name.lower().endswith(".pchtxt"):
                ids, title = _read_pchtxt(path)
                info.ids |= ids
                info.files += 1
                info.title = info.title or title
    return info


def _is_ips(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(5) in IPS_MAGIC
    except OSError:
        return False


def _read_pchtxt(path: str) -> tuple[set[str], str]:
    """pchtxt 里的 Build ID（@nsobid-xxx），和开头注释里的游戏名（比如 "# The Legend of Zelda: TotK 1.2.1 - 60 FPS"）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(65536)
    except OSError:
        return set(), ""
    title = ""
    for line in text.splitlines()[:40]:
        m = TITLE_LINE.match(line)
        if m and not m.group(1).startswith("@"):
            title = m.group(1)[:80]
            break
    return {key(i) for i in NSOBID.findall(text)}, title


def judge(name: str, info: PatchFolder, system_ids: set[str]) -> str | None:
    """返回不用留的原因（needs.REASONS 的键）；是游戏 MOD 返回 None。"""
    if not info.files:
        return "nopatch"
    if info.ids & system_ids or name.lower() in SYSTEM_PATCH_DIRS:
        return "syspatch"
    return None


def describe(info: PatchFolder) -> str:
    """给界面看的一句话。"""
    text = f"{info.files} 个补丁"
    return f"{info.title} · {text}" if info.title else text
