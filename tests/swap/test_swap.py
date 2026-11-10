"""换包：每一项保留还是清除、不是存储卡时只动大气层文件夹、防电脑硬盘、真正执行一次清理和装包、快照恢复。

存储卡用临时文件夹假扮（环境变量 NXTOOLBOX_FAKE_SD），不碰真实的卡。
"""

import os
import shutil
import struct
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import support  # noqa: E402
from support import write  # noqa: E402

api = support.load_tool("swap")
from core import rules, sdcard  # noqa: E402  （要在加载工具之后，core 才是换包的）

KEEP, DEL = True, False


def nsp(names: list[str]) -> bytes:
    """一个只有目录的 nsp（PFS0）。带 .tik 的算正版游戏。"""
    table = b"".join(n.encode() + b"\0" for n in names)
    head = b"PFS0" + struct.pack("<III", len(names), len(table), 0)
    entries, offset = b"", 0
    for name in names:
        entries += struct.pack("<QQII", 0, 0, offset, 0)
        offset += len(name) + 1
    return head + entries + table


def build_card(root: Path) -> None:
    files = {
        "atmosphere/package3": b"PK31" + bytes(60),
        "atmosphere/whitelist.txt": "switch/MyTool\n",
        "atmosphere/blacklist.txt": "config/sys-clk\n",
        "atmosphere/contents/0100F2C0115B6000/cheats/abc.txt": "cheat",
        "atmosphere/contents/420000000007E51A/exefs.nsp": "black",
        "atmosphere/contents/0100BBBB00000000/exefs.nsp": "old module",
        "bootloader/hekate_ipl.ini": "[config]",
        "bootloader/ini/android.ini": "[Android]\npayload=switchroot/android/coreboot.rom\n",
        "bootloader/ini/other.ini": "[Other]\npayload=bootloader/payloads/x.bin\n",
        "switchroot/android/boot.img": "img",
        "config/JKSV/config.ini": "cfg",
        "switch/JKSV/JKSV.nro": "nro",
        "JKSV/Zelda/save.zip": "save",
        "switch/DBI/DBI.nro": "nro",
        "switch/ppsspp/PPSSPP.nro": "nro",
        "switch/GBAStation.nro": "nro",
        "roms/gba/pokemon.gba": "rom",
        "switch/MyTool/MyTool.nro": "nro",
        "config/sys-clk/config.ini": "cfg",
        "config/ftpsrv/config.ini": "cfg",
        "emuMMC/RAW1/raw_based": b"\0\0\0\0",
        "Nintendo/Contents/registered/a.nca": "nca",
        "switch/prod.keys": "keys",
        "games/Zelda.xci": "xci",
        "games/readme.txt": "readme",
        "Mario.nsp": nsp(["a.nca", "abc.tik", "abc.cert"]),
        "front.nsp": nsp(["control.nca", "program.nca"]),
        "Videos/clip.mp4": "video",
        "ceshi/notes.txt": "notes",
        "System Volume Information/IndexerVolumeGuid": "x",
    }
    for rel, data in files.items():
        write(root, rel, data)
    (root / "atmosphere/contents/0100AAAA00000000/romfs").mkdir(parents=True)     # 只有空文件夹的 MOD
    (root / "emptyfolder").mkdir()


# 存储卡上，没装新包时：每一项应该怎样
EXPECTED = {
    "atmosphere/package3": DEL,                                        # 其余一律清除
    "atmosphere/whitelist.txt": KEEP,                                  # 名单文件本身留着
    "atmosphere/contents/0100F2C0115B6000": KEEP,                      # 金手指
    "atmosphere/contents/420000000007E51A": DEL,                       # 黑名单
    "atmosphere/contents/0100BBBB00000000": DEL,                       # 旧的系统模块
    "atmosphere/contents/0100AAAA00000000": DEL,                       # 只有空文件夹，没有 MOD
    "bootloader/hekate_ipl.ini": DEL,
    "bootloader/ini/android.ini": KEEP,                                # 有 switchroot 的安卓启动项
    "bootloader/ini/other.ini": DEL,                                   # 不是安卓和 Linux 的启动项
    "switchroot": KEEP,
    "config/JKSV": KEEP, "switch/JKSV": KEEP, "JKSV": KEEP,            # 有存档，存档工具留着
    "switch/DBI": DEL,
    "switch/ppsspp": DEL,                                              # 没有 PSP 的游戏
    "switch/GBAStation.nro": KEEP,                                     # 有 gba 游戏
    "roms": KEEP,
    "switch/MyTool": KEEP,                                             # 卡上的白名单
    "config/sys-clk": DEL,                                             # 卡上的黑名单
    "config/ftpsrv": DEL,                                              # 用它的插件不在了
    "emuMMC": KEEP, "Nintendo": KEEP,
    "switch/prod.keys": KEEP,
    "games/Zelda.xci": KEEP, "games/readme.txt": DEL,                  # 普通文件夹里的游戏留下，别的清
    "Mario.nsp": KEEP,                                                 # 带门票的 nsp 是游戏
    "front.nsp": DEL,                                                  # 不带门票的是插件前端
    "Videos/clip.mp4": KEEP,
    "ceshi": DEL,
    "emptyfolder": DEL,
}


def wait(timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = api.progress()
        if not state.get("running"):
            return state
        time.sleep(0.1)
    raise TimeoutError("任务没有结束")


class CardTest(unittest.TestCase):
    """假扮成存储卡的文件夹。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="nxswap_card_"))
        build_card(self.root)
        os.environ[sdcard.FAKE_SD_ENV] = str(self.root)
        api._save(groups={}, user={})
        api.use_source("none")
        self.card = str(self.root)

    def tearDown(self):
        os.environ.pop(sdcard.FAKE_SD_ENV, None)
        shutil.rmtree(self.root, ignore_errors=True)

    def verdict(self, path: str) -> bool:
        return api._rules(self.card).evaluate(rules.norm(path)).keep

    def nodes(self) -> dict[str, bool]:
        return {n.path.replace("\\", "/"): n.verdict.keep for n in rules.scan(self.card, api._rules(self.card))}


class RulesOnCardTest(CardTest):
    def test_every_item(self):
        for path, keep in EXPECTED.items():
            with self.subTest(path=path):
                self.assertEqual(self.verdict(path), keep)

    def test_scan_matches_and_skips_windows_folders(self):
        nodes = self.nodes()
        self.assertFalse(any(p.lower().startswith("system volume information") for p in nodes))
        for path, keep in EXPECTED.items():
            if path in nodes:
                with self.subTest(path=path):
                    self.assertEqual(nodes[path], keep)
        self.assertIn("games/Zelda.xci", nodes, "放着游戏的普通文件夹要往里走，不能整个清掉")

    def test_plan(self):
        plan = api.plan(self.card)
        self.assertTrue(plan["card_mode"])
        ids = [g["id"] for g in plan["groups"]]
        for gid in ("emu", "keys", "saves", "cheats", "games", "emus", "media", "linux", "black", "rest"):
            self.assertIn(gid, ids)
        deletes = sum(n.files for n in rules.scan(self.card, api._rules(self.card)) if not n.verdict.keep)
        self.assertEqual(plan["totals"]["delete"], deletes)
        self.assertGreater(deletes, 0)

    def test_turn_a_group_off_and_on(self):
        api.set_group("saves", False)
        self.assertEqual(self.verdict("JKSV"), DEL)
        self.assertEqual(self.verdict("switch/JKSV"), DEL)
        api.set_group("saves", True)
        self.assertEqual(self.verdict("JKSV"), KEEP)

    def test_manual_rules_win(self):
        api.set_rule(self.card, "ceshi", True)
        self.assertEqual(self.verdict("ceshi"), KEEP)
        api.set_rule(self.card, "config/sys-clk", True)            # 手动保留压过黑名单
        self.assertEqual(self.verdict("config/sys-clk"), KEEP)
        api.set_rule(self.card, "roms", False)
        self.assertEqual(self.verdict("roms"), DEL)
        api.clear_rule("ceshi")
        self.assertEqual(self.verdict("ceshi"), DEL)

    def test_rule_same_as_default_is_not_stored(self):
        api.set_rule(self.card, "ceshi", False)
        self.assertNotIn("ceshi", api._settings()["user"])

    def test_children(self):
        items = {i["path"]: i["keep"] for i in api.children(self.card, "switch")}
        self.assertEqual(items["switch/DBI"], DEL)
        self.assertEqual(items["switch/MyTool"], KEEP)
        root = [i["path"] for i in api.children(self.card, "")]
        self.assertNotIn("System Volume Information", root)


class RunOnCardTest(CardTest):
    def test_cleanup_then_restore_snapshot(self):
        plan = api.plan(self.card)
        started = api.start(self.card, plan["totals"]["delete"])
        self.assertTrue(started.get("ok"), started)
        state = wait()
        self.assertIsNone(state["error"], state)
        for path, keep in EXPECTED.items():
            with self.subTest(path=path):
                self.assertEqual((self.root / path).exists(), keep)
        self.assertTrue((self.root / "System Volume Information").exists(), "Windows 的文件夹不动")
        # 快照里有清掉的东西，恢复回来
        snaps = api.snapshots()["items"]
        self.assertTrue(snaps)
        restored = api.snapshot_restore(snaps[0]["id"], self.card)
        self.assertTrue(restored.get("ok"), restored)
        self.assertIsNone(wait()["error"])
        self.assertEqual((self.root / "ceshi/notes.txt").read_text(encoding="utf-8"), "notes")
        self.assertTrue((self.root / "switch/DBI/DBI.nro").is_file())

    def test_count_changed_since_confirm(self):
        plan = api.plan(self.card)
        result = api.start(self.card, plan["totals"]["delete"] + 1)
        self.assertTrue(result.get("replan"), result)
        self.assertTrue((self.root / "ceshi").exists())

    def test_install_package(self):
        archive = support.temp_dir(self, "nxswap_pkg_") / "package.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("atmosphere/package3", b"PK31" + bytes(60))
            z.writestr("bootloader/hekate_ipl.ini", "[new]")
            z.writestr("switch/NewTool/NewTool.nro", "new")
            z.writestr("payload.bin", "payload")
        source = api.use_source("local", str(archive))
        self.assertEqual(source["kind"], "local", source)
        plan = api.plan(self.card)
        self.assertGreater(plan["totals"]["install"], 0)
        self.assertTrue(api.start(self.card, plan["totals"]["delete"]).get("ok"))
        state = wait()
        self.assertIsNone(state["error"], state)
        self.assertEqual((self.root / "bootloader/hekate_ipl.ini").read_text(encoding="utf-8"), "[new]")
        self.assertTrue((self.root / "switch/NewTool/NewTool.nro").is_file())
        self.assertFalse((self.root / "ceshi").exists())
        self.assertTrue((self.root / "Nintendo/Contents/registered/a.nca").is_file())


class NotACardTest(unittest.TestCase):
    """普通文件夹（电脑硬盘、移动硬盘上的）：只在大气层自己的文件夹里清黑名单。"""

    def setUp(self):
        os.environ.pop(sdcard.FAKE_SD_ENV, None)
        self.root = Path(tempfile.mkdtemp(prefix="nxswap_folder_"))
        build_card(self.root)
        api._save(groups={}, user={})
        api.use_source("none")
        self.card = str(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_only_blacklist_inside_switch_folders(self):
        self.assertFalse(sdcard.is_card(self.card))
        ruleset = api._rules(self.card)
        self.assertEqual(ruleset.evaluate("config/sys-clk").keep, DEL)
        self.assertEqual(ruleset.evaluate("atmosphere/contents/420000000007e51a").keep, DEL)
        for path in ("ceshi", "switch/dbi", "atmosphere/package3", "front.nsp", "emptyfolder"):
            with self.subTest(path=path):
                verdict = ruleset.evaluate(path)
                self.assertTrue(verdict.keep)
                self.assertEqual(verdict.why, "pc")
        self.assertFalse(ruleset.may_delete("ceshi"))

    def test_plan_says_folder_mode(self):
        plan = api.plan(self.card)
        self.assertFalse(plan["card_mode"])
        rest = next(g for g in plan["groups"] if g["id"] == "rest")
        self.assertTrue(rest.get("fixed"))

    def test_run_leaves_other_files(self):
        plan = api.plan(self.card)
        self.assertTrue(api.start(self.card, plan["totals"]["delete"]).get("ok"))
        self.assertIsNone(wait()["error"])
        self.assertFalse((self.root / "config/sys-clk").exists())
        for path in ("ceshi/notes.txt", "switch/DBI/DBI.nro", "atmosphere/package3", "front.nsp"):
            self.assertTrue((self.root / path).exists(), path)


class ComputerDriveTest(unittest.TestCase):
    """电脑硬盘和系统文件夹直接拒绝。"""

    def test_system_drive(self):
        from nxhost import cards
        letter = cards.system_letter()
        self.assertIn("error", api.plan(letter))
        self.assertIn("error", api.start(letter))
        self.assertIn("error", api.card(letter))

    def test_folder_that_looks_like_a_pc_drive(self):
        root = Path(tempfile.mkdtemp(prefix="nxswap_pc_"))
        try:
            build_card(root)
            (root / "Windows" / "System32").mkdir(parents=True)
            (root / "Program Files").mkdir()
            self.assertIn("error", api.plan(str(root)))
            self.assertIn("error", api.start(str(root)))
            self.assertTrue((root / "ceshi").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_missing_folder(self):
        self.assertIn("error", api.card(str(Path(tempfile.gettempdir()) / "does-not-exist-nxswap")))

    def test_detect_never_lists_computer_drives(self):
        from nxhost import cards
        os.environ.pop(sdcard.FAKE_SD_ENV, None)
        found = [c["path"] for c in api.cards()]
        self.assertNotIn(cards.system_letter(), found)
        for path in found:
            self.assertTrue(cards.drive_is_card(path), path)


if __name__ == "__main__":
    unittest.main()
