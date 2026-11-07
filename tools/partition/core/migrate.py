"""换卡的整个过程：看旧卡、把旧卡存到电脑、写新卡。

旧卡全程只读。两个读卡器时旧卡直接当来源；只有一个读卡器时先把旧卡存到电脑上的一个文件夹，
换上新卡后再拿这个文件夹当来源。两种来源对"写新卡"来说是一样的。
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from core import clone, disk, emucfg, emufile, fat32, layout, userfs
from core.layout import ALIGN_SECTORS, BOOT_SECTORS, MB_SECTORS, SECTOR, Card, Partition, RawEmummc
from core.xts import UNIT, Xts

SKIP_NAMES = {"system volume information", "$recycle.bin"}     # Windows 自己放在卡上的，不搬
CLUSTER = fat32.CLUSTER_BYTES
FAT_SLACK = 64 * 1024 * 1024          # 给 FAT 表和对齐留的余量
FAT_OVERHEAD = 1.002
COPY_CHUNK = 4 * 1024 * 1024
MANIFEST = "manifest.json"
FILES_DIR = "files"
_KEEP_ATTRS = 0x1 | 0x2 | 0x4 | 0x20  # 只读、隐藏、系统、存档
_INVALID_ATTRS = 0xFFFFFFFF
STEP_SAVE_PARTS, STEP_SAVE_FILES, STEP_SAVE_VERIFY = "保存分区", "保存文件", "核对存到电脑的内容"
STEP_FILE_EMU, STEP_REFORMAT = "调整文件式虚拟系统", "重新分区"
STEP_FORMAT, STEP_PARTS, STEP_FILES, STEP_VERIFY, STEP_FINISH = "给新卡分区", "写入分区", "复制文件", "核对文件", "收尾"

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
_k32.GetFileAttributesW.restype = ctypes.c_uint32
_k32.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]


class MigrateError(Exception):
    """文字直接给用户看。"""


# ---- 文件 ----

def _copy_attrs(source: str, target: str) -> None:
    """存档、隐藏这些属性要带过去：系统靠文件夹上的"存档"属性认拆分的大文件。"""
    attrs = _k32.GetFileAttributesW(source)
    if attrs != _INVALID_ATTRS:
        _k32.SetFileAttributesW(target, (attrs & _KEEP_ATTRS) or 0x80)


def walk(root: Path):
    """卡上的每个文件夹和文件：(相对路径, 是不是文件夹, 大小)。文件夹排在它里面的东西前面。"""
    stack = [""]
    while stack:
        rel = stack.pop()
        try:
            entries = sorted(os.scandir(root / rel), key=lambda e: e.name.lower())
        except OSError as exc:
            raise MigrateError(f"读不了卡上的文件夹 {rel or '/'}：{exc.strerror or exc}") from exc
        for entry in entries:
            if not rel and entry.name.lower() in SKIP_NAMES:
                continue
            path = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir(follow_symlinks=False):
                yield path, True, 0
                stack.append(path)
            else:
                yield path, False, entry.stat(follow_symlinks=False).st_size


def measure(root: Path) -> dict:
    files = folders = size = need = 0
    for _, is_dir, length in walk(root):
        if is_dir:
            folders += 1
            need += CLUSTER
        else:
            files += 1
            size += length
            need += -(-length // CLUSTER) * CLUSTER          # 新卡用 64 KB 簇，小文件占得比原来多
    return {"files": files, "folders": folders, "bytes": size, "need": int(need * FAT_OVERHEAD) + FAT_SLACK}


def copy_tree(job, source: Path, target: Path, total: int, digests: dict | None, files: int = 0, skip: set[str] = frozenset()) -> int:
    """把 source 下的东西搬到 target 下。digests 不是 None 时记下每个文件的校验值，留着核对。files 是文件总数，显示用。
    skip 里的文件（相对路径，小写）不搬。"""
    done = count = 0
    folders = []
    for rel, is_dir, _ in walk(source):
        src, dst = source / rel, target / rel
        if rel.lower() in skip:
            continue
        if is_dir:
            dst.mkdir(parents=True, exist_ok=True)
            folders.append(rel)
            continue
        digest = hashlib.blake2b(digest_size=16) if digests is not None else None
        with open(src, "rb") as reader, open(dst, "wb") as writer:
            while True:
                job.check()
                data = reader.read(COPY_CHUNK)
                if not data:
                    break
                writer.write(data)
                if digest:
                    digest.update(data)
                done += len(data)
                job.progress(done / total if total else 1.0, _amount(done, total), rel, _nth(count, files, "个文件"))
        if not total:
            job.progress(1.0, "", rel, _nth(count, files, "个文件"))
        shutil.copystat(src, dst)
        _copy_attrs(str(src), str(dst))
        if digests is not None:
            digests[rel] = digest.hexdigest()
        count += 1
    for rel in reversed(folders):                               # 文件夹的属性最后设，里面的东西都放好了
        _copy_attrs(str(source / rel), str(target / rel))
    job.note(f"{count:,} 个文件，{_gb(total)}")
    return count


def verify_tree(job, root: Path, digests: dict, total: int, start: float = 0.0, share: float = 1.0) -> None:
    """start、share：这一步的进度从哪里开始、占多少，前面还有别的要核对时用。"""
    done = 0
    for index, (rel, expected) in enumerate(digests.items()):
        digest = hashlib.blake2b(digest_size=16)
        try:
            with open(root / rel, "rb") as reader:
                while True:
                    job.check()
                    data = reader.read(COPY_CHUNK)
                    if not data:
                        break
                    digest.update(data)
                    done += len(data)
                    job.progress(start + share * (done / total if total else 1.0), _amount(done, total), rel,
                                 _nth(index, len(digests), "个文件"))
        except OSError as exc:
            raise MigrateError(f"核对时读不出 {rel}，新卡可能有问题") from exc
        if digest.hexdigest() != expected:
            raise MigrateError(f"{rel} 写到新卡之后内容对不上，新卡或读卡器可能有问题")
    job.note(f"{len(digests):,} 个文件都一致")


def _gb(n: int) -> str:
    return f"{n / 1073741824:.2f} GB"


def _nth(index: int, total: int, unit: str) -> str:
    return f"第 {index + 1:,} / {total:,} {unit}" if total else ""


def _part_label(kinds: list[str], index: int) -> str:
    """"虚拟系统"、"Linux" 这样的名字。同一种有好几个时带上序号。"""
    name = layout.KIND_NAMES[kinds[index]]
    same = [i for i, k in enumerate(kinds) if k == kinds[index]]
    return f"{name} {same.index(index) + 1}" if len(same) > 1 else name


def _amount(done: int, total: int) -> str:
    return f"{done / 1073741824:.2f} / {total / 1073741824:.2f} GB"


# ---- 卡的信息 ----

def _card_to_json(card: Card) -> dict:
    return {"total": card.total, "fat": asdict(card.fat), "extras": [asdict(p) for p in card.extras],
            "emummcs": [{"index": card.extras.index(e.partition), "sector": e.sector, "used": e.used} for e in card.emummcs],
            "mbr": card.mbr.hex(), "gpt_header": card.gpt_header.hex() if card.gpt_header else None,
            "gpt_entries": card.gpt_entries.hex() if card.gpt_entries else None}


def _card_from_json(data: dict) -> Card:
    extras = [Partition(**p) for p in data["extras"]]
    return Card(data["total"], Partition(**data["fat"]), extras,
                [RawEmummc(extras[e["index"]], e["sector"], e["used"]) for e in data["emummcs"]], bytes.fromhex(data["mbr"]),
                bytes.fromhex(data["gpt_header"]) if data["gpt_header"] else None,
                bytes.fromhex(data["gpt_entries"]) if data["gpt_entries"] else None)


def _copy_lengths(card: Card) -> list[int]:
    used = {e.partition: e.used for e in card.emummcs}
    return [used.get(p, p.size) for p in card.extras]


def _summary(card: Card, files: dict, root: Path | None) -> dict:
    lengths = _copy_lengths(card)
    extras = [{"kind": card.kind(p), "name": p.name, "bytes": p.size * SECTOR, "used": n * SECTOR}
              for p, n in zip(card.extras, lengths)]
    kinds = emucfg.kinds(root) if root else {"raw": [], "file": []}
    return {"files": files, "extras": extras, "emu_file": kinds["file"], "emu_raw": len(card.emummcs),
            "fat_bytes": card.fat.size * SECTOR,
            "copy_bytes": files["bytes"] + sum(lengths) * SECTOR,
            "min_bytes": (ALIGN_SECTORS + layout.span_sectors(card) + MB_SECTORS) * SECTOR + files["need"]}


def _open_user(source, index: int):
    """打开第 index 个分区里的 USER：(设备, 分区在设备上的起点, 虚拟系统在分区里的偏移扇区, 镜像, 加解密, 明文读写, 文件系统)。"""
    emu = next(e for e in source.card.emummcs if e.partition == source.card.extras[index])
    keys = userfs.read_keys(source.root)
    if not keys:
        raise userfs.UserError("卡上没有 switch/prod.keys")
    device, offset = source.open_part(index)
    inner = emu.sector - emu.partition.start
    base = offset + (inner + BOOT_SECTORS) * SECTOR
    nand = userfs.read_nand(device, base)
    xts = Xts(*keys)
    plain = userfs.Plain(device, base + nand.user_start * SECTOR, xts)
    return device, offset, inner, nand, xts, plain, userfs.read_fat(plain)


def _open_file_emu(root: Path, name: str):
    """打开文件形式的虚拟系统：(设备, 镜像, 加解密, 明文读写, 文件系统)。用完要关设备。"""
    keys = userfs.read_keys(root)
    if not keys:
        raise userfs.UserError("卡上没有 switch/prod.keys")
    device = emufile.SplitFiles(emufile.gpp_paths(emufile.emmc_dir(root, name)))
    try:
        nand = userfs.read_nand(device, 0)
        xts = Xts(*keys)
        plain = userfs.Plain(device, nand.user_start * SECTOR, xts)
        return device, nand, xts, plain, userfs.read_fat(plain)
    except BaseException:
        device.close()
        raise


def _round_mb(sectors: int) -> int:
    return -(-sectors // MB_SECTORS) * MB_SECTORS


def _round_align(sectors: int) -> int:
    """按 16 MB 取整：分区按 16 MB 对齐，后面的分区才能对齐。"""
    return -(-sectors // ALIGN_SECTORS) * ALIGN_SECTORS


def emu_details(source) -> list[dict]:
    """每个分区形式的虚拟系统：现在多大、USER 用了多少、最小能缩到多少。读不了密钥的只能原样搬。"""
    details = []
    for emu in source.card.emummcs:
        index = source.card.extras.index(emu.partition)
        item = {"index": index, "bytes": emu.partition.size * SECTOR, "resizable": False, "reason": ""}
        try:
            _, _, inner, nand, _, _, fat = _open_user(source, index)
            use = userfs.usage(fat)
            fixed = inner + BOOT_SECTORS + nand.user_start + userfs.GPT_SECTORS      # USER 以外占的扇区
            item.update(resizable=True, user_bytes=use["bytes"], user_used=use["used"], fixed_bytes=fixed * SECTOR,
                        min_bytes=min(emu.partition.size, _round_align(fixed + userfs.minimum_sectors(fat))) * SECTOR)
        except userfs.UserError as exc:
            item["reason"] = str(exc)
        except OSError as exc:
            item["reason"] = f"读不了这个虚拟系统：{exc.strerror or exc}"
        details.append(item)
    return details


def file_emu_details(root: Path) -> list[dict]:
    """每个文件形式的虚拟系统。bytes 是镜像文件（00、01……）加起来的大小，BOOT0、BOOT1 不算。"""
    details = []
    for name in emucfg.kinds(root)["file"]:
        paths = emufile.gpp_paths(emufile.emmc_dir(root, name))
        size = sum(path.stat().st_size for path in paths)
        item = {"name": name, "bytes": size, "resizable": False, "reason": ""}
        if paths:
            try:
                device, nand, _, _, fat = _open_file_emu(root, name)
                try:
                    use = userfs.usage(fat)
                    fixed = nand.user_start + userfs.GPT_SECTORS
                    item.update(resizable=True, user_bytes=use["bytes"], user_used=use["used"], fixed_bytes=fixed * SECTOR,
                                min_bytes=min(size // SECTOR, _round_align(fixed + userfs.minimum_sectors(fat))) * SECTOR)
                finally:
                    device.close()
            except userfs.UserError as exc:
                item["reason"] = str(exc)
            except OSError as exc:
                item["reason"] = f"读不了这个虚拟系统：{exc.strerror or exc}"
        else:
            item["reason"] = "找不到它的镜像文件（eMMC/00）"
        details.append(item)
    return details


def groups(card: Card, raw: list[dict], files: list[dict]) -> list[dict]:
    """页面上一行一个：分区形式的虚拟系统各一行、Linux 一行、Android 的那些分区合成一行、文件形式的虚拟系统各一行。
    id 是页面传回来的键；indices 是对应 card.extras 里的哪些分区。"""
    kinds = [card.kind(p) for p in card.extras]
    android = [i for i, kind in enumerate(kinds) if kind == "android"]
    many_emu = kinds.count("emummc") > 1
    out = []
    for index, part in enumerate(card.extras):
        kind = kinds[index]
        if kind == "android":
            if index != android[0]:
                continue
            out.append({"id": "android", "kind": "android", "indices": android, "resizable": False, "deletable": True,
                        "label": "Android" + (f"（{len(android)} 个分区）" if len(android) > 1 else ""),
                        "bytes": sum(card.extras[i].size for i in android) * SECTOR,
                        "reason": "Android 分区里的文件系统现在调不了大小，可以保留或删除"})
        elif kind == "linux":
            out.append({"id": f"p{index}", "kind": "linux", "indices": [index], "resizable": False, "deletable": True,
                        "label": _part_label(kinds, index), "bytes": part.size * SECTOR,
                        "reason": "Linux 分区里的文件系统现在调不了大小，可以保留或删除"})
        else:
            detail = next((d for d in raw if d["index"] == index), {"bytes": part.size * SECTOR, "resizable": False, "reason": ""})
            number = f" {[i for i, k in enumerate(kinds) if k == 'emummc'].index(index) + 1}" if many_emu else ""
            out.append({**detail, "id": f"p{index}", "kind": "emummc", "indices": [index], "deletable": True,
                        "label": f"虚拟系统{number}（分区式）"})
    for detail in files:
        out.append({**detail, "id": "f:" + detail["name"], "kind": "emufile", "indices": [], "deletable": False,
                    "label": f"虚拟系统（文件式 · {detail['name']}）"})
    return out


def _find_disk(number: int) -> dict:
    info = next((d for d in disk.list_disks() if d["number"] == number), None)
    if info is None:
        raise MigrateError("找不到这张卡了，可能被拔掉了")
    return info


def _fat_root(number: int, card: Card) -> Path:
    letter = next((v["letter"] for v in disk.volumes()
                   if v["disk"] == number and v["offset"] == card.fat.start * SECTOR and v["letter"]), None)
    if not letter:
        raise MigrateError("Windows 没有给这张卡的 FAT32 分区分配盘符，拔下来重新插一次试试")
    return Path(letter)


def inspect(number: int) -> dict:
    """看一张旧卡：有哪些分区、文件有多少、新卡至少要多大。"""
    info = _find_disk(number)
    source = DiskSource(number)
    try:
        return {"number": number, "name": info["name"], "size": info["size"], "letter": str(source.root),
                **_summary(source.card, measure(source.root), source.root),
                "parts": groups(source.card, emu_details(source), file_emu_details(source.root))}
    finally:
        source.close()


# ---- 来源 ----

class DiskSource:
    def __init__(self, number: int) -> None:
        info = _find_disk(number)
        self.number, self.name = number, info["name"]
        self._device = disk.open_disk(number, info["size"])
        self.card = layout.scan(self._device)
        self.root = _fat_root(number, self.card)

    def open_part(self, index: int):
        return self._device, self.card.extras[index].start * SECTOR

    def close(self) -> None:
        self._device.close()


class StagedSource:
    """存在电脑上的旧卡：manifest.json + 每个分区一个文件 + files 文件夹。"""
    number = None

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.manifest = read_stage(self.folder)
        if not self.manifest or not self.manifest.get("complete"):
            raise MigrateError("电脑上没有存好的旧卡内容")
        self.name = self.manifest["name"]
        self.card = _card_from_json(self.manifest["card"])
        self.root = self.folder / FILES_DIR
        self._open = []

    def open_part(self, index: int):
        device = clone.FileDevice(str(self.folder / f"part_{index}.bin"))
        self._open.append(device)
        return device, 0

    def close(self) -> None:
        for device in self._open:
            device.close()


def read_stage(folder: Path) -> dict | None:
    try:
        return json.loads((Path(folder) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stage_summary(folder: Path) -> dict | None:
    manifest = read_stage(folder)
    if not manifest or not manifest.get("complete"):
        return None
    card = _card_from_json(manifest["card"])
    return {"name": manifest["name"], "size": manifest["size"], "saved_at": manifest["saved_at"],
            **_summary(card, manifest["files"], Path(folder) / FILES_DIR),
            "parts": groups(card, manifest.get("raw_details", manifest.get("emus", [])), manifest.get("file_details", []))}


def discard_stage(folder: Path) -> None:
    folder = Path(folder)
    if (folder / MANIFEST).is_file() or not folder.exists() or not any(folder.iterdir()):
        shutil.rmtree(folder, ignore_errors=True)


# ---- 存到电脑 ----

def save_steps(summary: dict, verify: bool = False) -> list[str]:
    return ([STEP_SAVE_PARTS] if summary["extras"] else []) + [STEP_SAVE_FILES] + ([STEP_SAVE_VERIFY] if verify else [])


def save(job, number: int, folder: Path, verify: bool = False) -> dict:
    """把旧卡存到电脑。verify 时存完把电脑上的每个分区和文件再读一遍，和卡上读到的比对；
    单卡调整之后要清空这张卡，存下来的必须是对的。"""
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()) and not (folder / MANIFEST).is_file():
        raise MigrateError("存放的文件夹里已经有别的东西了，请换一个空文件夹")
    shutil.rmtree(folder, ignore_errors=True)
    source = DiskSource(number)
    try:
        files = measure(source.root)
        lengths = _copy_lengths(source.card)
        need = files["bytes"] + sum(lengths) * SECTOR
        folder.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(folder).free
        if free < need + FAT_SLACK:
            raise MigrateError(f"电脑上这个位置只剩 {free / 1073741824:.1f} GB，存旧卡需要 {need / 1073741824:.1f} GB")
        manifest = {"version": 1, "complete": False, "name": source.name, "size": _find_disk(number)["size"],
                    "saved_at": time.time(), "card": _card_to_json(source.card), "files": files,
                    "raw_details": emu_details(source), "file_details": file_emu_details(source.root)}
        (folder / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        total = sum(lengths) * SECTOR
        done = 0
        if lengths:
            job.step(STEP_SAVE_PARTS)
        for index, length in enumerate(lengths):
            path = folder / f"part_{index}.bin"
            path.write_bytes(b"")
            target = clone.FileDevice(str(path), writable=True, size=0)
            device, offset = source.open_part(index)

            label = _part_label([source.card.kind(p) for p in source.card.extras], index)

            def tick(count, base=done, label=label, size=length * SECTOR, index=index):
                job.check()
                job.progress((base + count) / total, _amount(count, size), f"{label} · {_gb(size)}",
                             f"第 {index + 1} / {len(lengths)} 个分区 · 复制到电脑")

            try:
                digest = clone.copy(device, offset, target, 0, length * SECTOR, tick)
            finally:
                target.close()
            manifest.setdefault("digests", []).append(digest)
            done += length * SECTOR
        if lengths:
            job.note(f"{len(lengths)} 个分区，{_gb(total)}")
        job.step(STEP_SAVE_FILES)
        digests = {} if verify else None
        copy_tree(job, source.root, folder / FILES_DIR, files["bytes"], digests, files["files"])
        if verify:
            job.step(STEP_SAVE_VERIFY)
            check_total = total + files["bytes"]
            checked = 0
            for index, length in enumerate(lengths):
                size = length * SECTOR
                device = clone.FileDevice(str(folder / f"part_{index}.bin"))

                def tick(count, base=checked, index=index, size=size):
                    job.check()
                    job.progress((base + count) / check_total, _amount(count, size), f"part_{index}.bin",
                                 f"第 {index + 1} / {len(lengths)} 个分区")

                try:
                    same = clone.checksum(device, 0, size, tick) == manifest["digests"][index]
                finally:
                    device.close()
                if not same:
                    raise MigrateError("存到电脑上的分区和卡上的对不上，电脑的硬盘或读卡器可能有问题")
                checked += size
            verify_tree(job, folder / FILES_DIR, digests, files["bytes"], checked / check_total if check_total else 0,
                        files["bytes"] / check_total if check_total else 1)
            job.note("存到电脑的内容都一致")
        manifest["complete"] = True
        (folder / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        return {"saved": True, "files": files["files"], "bytes": need}
    finally:
        source.close()


# ---- 写新卡 ----

def _is_same_card(card: Card, target: dict) -> bool:
    """经电脑中转时旧卡可能还插着：分区表和存下来的一模一样，就是旧卡本身。"""
    if target["size"] // SECTOR != card.total:
        return False
    try:
        device = disk.open_disk(target["number"], target["size"])
    except OSError:
        return False
    try:
        return device.read_at(0, SECTOR) == card.mbr
    finally:
        device.close()


def _sizes(summary: dict, sizes: dict | None) -> tuple[dict[int, int], dict[str, int]]:
    """页面给的新大小（字节，0 是删掉）→ (分区：第几个 → 扇区, 文件式虚拟系统：文件夹名 → 镜像的扇区数)。"""
    parts: dict[int, int] = {}
    files: dict[str, int] = {}
    for key, value in (sizes or {}).items():
        group = next((g for g in summary["parts"] if g["id"] == key), None)
        if group is None:
            raise MigrateError("卡上没有这个分区了，请重新读一次卡")
        value = int(value)
        if value == group["bytes"]:
            continue
        if value == 0:
            if not group["deletable"]:
                raise MigrateError(f"{group['label']}不能删除")
            for index in group["indices"]:
                parts[index] = 0
            continue
        if not group["resizable"]:
            raise MigrateError(f"{group['label']}的大小调不了：{group['reason']}")
        if value < group["min_bytes"]:
            raise MigrateError(f"{group['label']}最小只能缩到 {_gb(group['min_bytes'])}")
        sectors = _round_align(value // SECTOR)
        if group["kind"] == "emufile":
            files[group["name"]] = sectors
        else:
            parts[group["indices"][0]] = sectors
    return parts, files


def _cluster_bytes(n: int) -> int:
    return -(-n // CLUSTER) * CLUSTER


def check_target(summary: dict, card: Card, target: dict, source_number: int | None, sizes: dict | None = None,
                 same: bool = False) -> dict:
    """新卡行不行。行的话返回新卡会变成什么样，不行抛出原因。sizes：分区的 id → 新的大小（字节，0 是删掉）。
    same：单卡调整，写回的就是来源那张卡。"""
    if not same and (target["number"] == source_number or _is_same_card(card, target)):
        raise MigrateError("新卡和旧卡不能是同一张")
    if len(summary["extras"]) != len(card.extras) or summary["fat_bytes"] != card.fat.size * SECTOR:
        raise MigrateError("卡上的分区和刚才读到的不一样了，请重新选一次这张卡")
    parts, files = _sizes(summary, sizes)
    by_name = {g["name"]: g for g in summary["parts"] if g["kind"] == "emufile"}
    need = summary["files"]["need"] + sum(_cluster_bytes(n * SECTOR) - _cluster_bytes(by_name[k]["bytes"]) for k, n in files.items())
    files_bytes = summary["files"]["bytes"] + sum(n * SECTOR - by_name[k]["bytes"] for k, n in files.items())
    required = (ALIGN_SECTORS + layout.span_sectors(card, parts) + 2 * MB_SECTORS) * SECTOR + need
    if target["size"] < required:
        raise MigrateError(f"这张卡放不下，还差 {_gb(required - target['size'])}")
    try:
        plan = layout.plan(target["size"] // SECTOR, card, parts)
    except layout.LayoutError as exc:
        raise MigrateError(str(exc)) from exc
    spare = plan.fat.size * SECTOR - need
    if spare < 0:
        raise MigrateError(f"这张卡放不下旧卡的文件，还差 {_gb(-spare)}")
    out = []
    for group in summary["parts"]:
        if group["kind"] == "emufile":
            new = files.get(group["name"], group["bytes"] // SECTOR) * SECTOR
        else:
            new = sum(plan.moves[i].new_size for i in group["indices"]) * SECTOR
        top = (new + spare) // (ALIGN_SECTORS * SECTOR) * ALIGN_SECTORS * SECTOR if group["resizable"] and new else new
        out.append({**group, "new_bytes": new, "max_bytes": max(top, new)})
    return {"fat_bytes": plan.fat.size * SECTOR, "old_fat_bytes": card.fat.size * SECTOR, "files_bytes": files_bytes,
            "parts": out, "deleted": [g["label"] for g in out if not g["new_bytes"] and g["bytes"]],
            "extras": [{"kind": m.kind, "bytes": m.new_size * SECTOR} for m in plan.moves if m.new_size]}


def write_steps(summary: dict, verify: bool, sizes: dict | None = None, same: bool = False) -> list[str]:
    parts, files = _sizes(summary, sizes)
    kept = any(parts.get(i, 1) for i in range(len(summary["extras"])))
    return ([STEP_REFORMAT if same else STEP_FORMAT] + ([STEP_PARTS] if kept else []) + [STEP_FILES]
            + ([STEP_FILE_EMU] if files else []) + ([STEP_VERIFY] if verify else []) + [STEP_FINISH])


def write_partitions(job, source, plan, device, verify: bool) -> None:
    """把 FAT32 后面的分区写到新卡上：大小没变的原样搬，变了的重建里面的 USER，删掉的跳过。写完都读回来核对。"""
    kinds = [m.kind for m in plan.moves]
    order = [i for i, m in enumerate(plan.moves) if m.new_size]
    count = len(order)
    weights = {i: (plan.moves[i].length if plan.moves[i].new_size == plan.moves[i].old.size else plan.moves[i].new_size) for i in order}
    whole = sum(weights.values())
    for position, index in enumerate(order):
        move = plan.moves[index]
        name = layout.KIND_NAMES[move.kind]
        before = sum(weights[i] for i in order[:position]) / whole

        def tick(part: float, phase: str, done: int = 0, total: int = 0, label: str = f"{_part_label(kinds, index)} · {_gb(move.new_size * SECTOR)}",
                 before: float = before, share: float = weights[index] / whole, position: int = position):
            job.check()
            job.progress(before + min(1.0, part) * share, _amount(done, total) if total else "", label,
                         f"第 {position + 1} / {count} 个分区 · {phase}")

        if move.new_size == move.old.size:
            reader, offset = source.open_part(index)
            size = move.length * SECTOR
            digest = clone.copy(reader, offset, device, move.new_start * SECTOR, size,
                                lambda done: tick(done / size / 2, "原样复制", done, size))
            if clone.checksum(device, move.new_start * SECTOR, size,
                              lambda done: tick(0.5 + done / size / 2, "读回核对", done, size)) != digest:
                raise MigrateError(f"{name}分区写到新卡之后内容对不上，新卡或读卡器可能有问题")
            continue
        try:
            reader, offset, inner, nand, xts, old_plain, fat = _open_user(source, index)
            nand_sectors = move.new_size - inner - BOOT_SECTORS
            resize = userfs.plan(fat, userfs.user_sectors_for(nand, nand_sectors))
            target = move.new_start * SECTOR
            # USER 前面的部分（保护区、BOOT0/1、PRODINFO 到 SYSTEM）原样搬
            head = (inner + BOOT_SECTORS + nand.user_start) * SECTOR
            digest = clone.copy(reader, offset, device, target, head,
                                lambda done: tick(done / head * 0.1, "复制系统分区（PRODINFO 到 SYSTEM）", done, head))
            if clone.checksum(device, target, head,
                              lambda done: tick(0.1 + done / head * 0.1, "读回核对系统分区", done, head)) != digest:
                raise MigrateError(f"{name}分区写到新卡之后内容对不上，新卡或读卡器可能有问题")
            base = target + (inner + BOOT_SECTORS) * SECTOR
            new_plain = userfs.Plain(device, base + nand.user_start * SECTOR, xts)
            userfs.write_resized(old_plain, fat, resize, new_plain, lambda done, total: tick(0.2 + done / total * 0.4, f"重建系统空间 USER（调整到 {_gb(move.new_size * SECTOR)}）",
                                                    done * UNIT, total * UNIT), job.check)
            for sector, data in userfs.nand_tables(nand, nand_sectors):
                device.write_at(base + sector * SECTOR, data)
            device.write_at(target + (inner + userfs.NAND_PATROL_SECTOR) * SECTOR, bytes(SECTOR))
            device.flush()
            userfs.verify(fat, new_plain, resize, old_plain if verify else None,
                          lambda done, total: tick(0.6 + done / max(1, total) * 0.4, "核对系统空间里的内容",
                                                   done * fat.cluster_bytes, total * fat.cluster_bytes), job.check)
        except userfs.UserError as exc:
            raise MigrateError(str(exc)) from exc
    resized = sum(1 for i in order if plan.moves[i].new_size != plan.moves[i].old.size)
    deleted = len(plan.moves) - count
    job.note(f"{count} 个分区" + (f"，{resized} 个调整了大小" if resized else "") + (f"，删掉 {deleted} 个" if deleted else "") + "，都核对过")


def _file_emu_skip(root: Path, files: dict[str, int]) -> tuple[set[str], int, int]:
    """要重建的文件式虚拟系统：复制文件时跳过它们的镜像文件。返回 (跳过的相对路径, 个数, 字节数)。"""
    skip, size = set(), 0
    for name in files:
        for path in emufile.gpp_paths(emufile.emmc_dir(root, name)):
            skip.add(path.relative_to(root).as_posix().lower())
            size += path.stat().st_size
    return skip, len(skip), size


def write_file_emus(job, source_root: Path, target_root: Path, files: dict[str, int], verify: bool, digests: dict | None = None) -> None:
    """文件形式的虚拟系统按新大小重建：镜像前面的部分原样复制，USER 重建，分区表改成新的大小。
    BOOT0 里的巡检记录会清掉，digests（复制文件时记的校验值）里它的那一项要跟着更新，不然后面核对会对不上。"""
    names = list(files)
    for position, name in enumerate(names):
        nand_sectors = files[name]
        label = f"emuMMC/{name}/eMMC · {_gb(nand_sectors * SECTOR)}"

        def tick(part: float, phase: str, done: int = 0, total: int = 0, position: int = position, label: str = label):
            job.check()
            job.progress((position + min(1.0, part)) / len(names), _amount(done, total) if total else "", label,
                         f"第 {position + 1} / {len(names)} 个 · {phase}")

        try:
            reader, nand, xts, old_plain, fat = _open_file_emu(source_root, name)
            try:
                resize = userfs.plan(fat, userfs.user_sectors_for(nand, nand_sectors))
                folder = emufile.emmc_dir(target_root, name)
                tick(0, "给镜像文件分配空间")
                writer = emufile.create(folder, nand_sectors * SECTOR)
                try:
                    head = nand.user_start * SECTOR
                    digest = clone.copy(reader, 0, writer, 0, head, lambda done: tick(done / head * 0.1, "复制系统分区（PRODINFO 到 SYSTEM）", done, head))
                    if clone.checksum(writer, 0, head, lambda done: tick(0.1 + done / head * 0.1, "读回核对系统分区", done, head)) != digest:
                        raise MigrateError("文件式虚拟系统写到新卡之后内容对不上，新卡或读卡器可能有问题")
                    new_plain = userfs.Plain(writer, head, xts)
                    userfs.write_resized(old_plain, fat, resize, new_plain,
                                         lambda done, total: tick(0.2 + done / total * 0.4, f"重建系统空间 USER（调整到 {_gb(nand_sectors * SECTOR)}）",
                                                                  done * UNIT, total * UNIT), job.check)
                    for sector, data in userfs.nand_tables(nand, nand_sectors):
                        writer.write_at(sector * SECTOR, data)
                    writer.flush()
                    userfs.verify(fat, new_plain, resize, old_plain if verify else None,
                                  lambda done, total: tick(0.6 + done / max(1, total) * 0.4, "核对系统空间里的内容",
                                                           done * fat.cluster_bytes, total * fat.cluster_bytes), job.check)
                finally:
                    writer.close()
            finally:
                reader.close()
            boot0 = folder / "BOOT0"
            if boot0.is_file():                                    # 改了大小要清掉巡检记录，和分区式一样
                with open(boot0, "r+b") as handle:
                    handle.seek(userfs.NAND_PATROL_SECTOR * SECTOR)
                    handle.write(bytes(SECTOR))
                if digests is not None:
                    rel = boot0.relative_to(target_root).as_posix().lower()
                    key = next((k for k in digests if k.lower() == rel), None)
                    if key is not None:
                        digests[key] = hashlib.blake2b(boot0.read_bytes(), digest_size=16).hexdigest()
        except userfs.UserError as exc:
            raise MigrateError(str(exc)) from exc
    job.note(f"{len(names)} 个，都核对过")


def write(job, source, target_number: int, target_size: int, verify: bool, sizes: dict | None = None, same: bool = False) -> dict:
    """把来源写到新卡上。target_size 是用户确认时看到的容量，对不上就不写。sizes：分区的 id → 新的大小（字节，0 是删掉）。
    same：单卡调整，来源是这张卡存在电脑上的内容，写回这张卡。"""
    card = source.card
    target = _find_disk(target_number)
    if target["size"] != target_size:
        raise MigrateError("新卡和刚才确认的不是同一张了，请重新选一次")
    files = measure(source.root)
    summary = {**_summary(card, files, source.root), "parts": groups(card, emu_details(source), file_emu_details(source.root))}
    check_target(summary, card, target, source.number, sizes, same)
    total_sectors = target["size"] // SECTOR
    part_sizes, file_sizes = _sizes(summary, sizes)
    plan = layout.plan(total_sectors, card, part_sizes)

    job.step(STEP_REFORMAT if same else STEP_FORMAT, danger=True)
    with disk.locked(target_number):
        device = disk.open_disk(target_number, target["size"], writable=True)
        try:
            device.write_at(0, bytes(ALIGN_SECTORS * SECTOR))                       # 开头 16 MB 清零，旧的分区表不能留
            device.write_at((total_sectors - MB_SECTORS) * SECTOR, bytes(MB_SECTORS * SECTOR))
            for sector, data in plan.tables:
                device.write_at(sector * SECTOR, data)
            job.progress(0, "", "", "清掉旧的分区表，写入新的")
            formatted = fat32.format_partition(device, plan.fat.start, plan.fat.size,
                                   progress=lambda frac: job.progress(frac, "", "", f"建立放文件的 FAT32（{_gb(plan.fat.size * SECTOR)}）"))
            job.note(f"放文件的空间 {_gb(plan.fat.size * SECTOR)}")
            device.flush()
            if any(m.new_size for m in plan.moves):
                job.step(STEP_PARTS)
                write_partitions(job, source, plan, device, verify)
        finally:
            device.close()
    disk.rescan(target_number)
    letter = disk.wait_for_volume(target_number, plan.fat.start * SECTOR, serial=formatted["serial"])
    if not letter:
        raise MigrateError("新卡分好区了，但 Windows 没有把它挂出来。把新卡拔下来重新插上，再重新开始一次")
    root = Path(letter)

    job.step(STEP_FILES)
    digests = {} if verify else None
    skip, skipped, skipped_bytes = _file_emu_skip(source.root, file_sizes)
    count = copy_tree(job, source.root, root, files["bytes"] - skipped_bytes, digests, files["files"] - skipped, skip)
    if file_sizes:
        job.step(STEP_FILE_EMU)
        write_file_emus(job, source.root, root, file_sizes, verify, digests)
    if verify:
        job.step(STEP_VERIFY)
        verify_tree(job, root, digests, files["bytes"] - skipped_bytes)
    job.step(STEP_FINISH)
    job.progress(0.5, "", "", "把虚拟系统的位置改成新卡上的")
    changed = [name for old, new in plan.sectors if new is not None for name in emucfg.fix_sector(root, old, new)]
    disabled = [name for old, new in plan.sectors if new is None for name in emucfg.disable(root, old)]
    changed += disabled
    job.progress(1.0)
    job.note(f"改了 {len(changed)} 个配置文件" if changed else "")
    return {"letter": letter, "files": count, "bytes": files["bytes"], "parts": sum(1 for m in plan.moves if m.new_size), "changed": changed,
            "resized": sum(1 for m in plan.moves if m.new_size and m.new_size != m.old.size) + len(file_sizes),
            "deleted": sum(1 for m in plan.moves if not m.new_size), "emu_disabled": bool(disabled),
            "fat_bytes": plan.fat.size * SECTOR, "name": target["name"], "size": target["size"]}
