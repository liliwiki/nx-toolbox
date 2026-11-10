"""共用的存储卡识别（nxhost/cards.py）。"""

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import support  # noqa: E402,F401
from nxhost import cards  # noqa: E402


def descriptor_bytes(removable: bool, bus: int, vendor: str = "", product: str = "") -> bytes:
    """照 STORAGE_DEVICE_DESCRIPTOR 的样子拼一段，字符串放在结构体后面。"""
    head = bytearray(40)
    tail = b""
    offsets = []
    for text in (vendor, product):
        if text:
            offsets.append(len(head) + len(tail))
            tail += text.encode("ascii") + b"\0"
        else:
            offsets.append(0)
    struct.pack_into("<II", head, 0, 1, len(head) + len(tail))
    head[10] = int(removable)
    struct.pack_into("<II", head, 12, *offsets)
    struct.pack_into("<I", head, 28, bus)
    return bytes(head) + tail


class DescriptorTest(unittest.TestCase):
    def test_parse(self):
        desc = cards.parse_descriptor(descriptor_bytes(True, cards.BUS_USB, "Generic", "SD/MMC Reader"))
        self.assertEqual(desc, cards.Descriptor(True, cards.BUS_USB, "Generic", "SD/MMC Reader"))
        self.assertEqual(desc.name, "Generic SD/MMC Reader")

    def test_name_falls_back(self):
        self.assertEqual(cards.parse_descriptor(descriptor_bytes(True, cards.BUS_USB)).name, "存储卡")

    def test_too_short(self):
        self.assertIsNone(cards.parse_descriptor(b"\0" * 20))
        self.assertIsNone(cards.parse_descriptor(None))


class WhatCountsAsCardTest(unittest.TestCase):
    def check(self, removable, bus, expected, include_virtual=False):
        desc = cards.parse_descriptor(descriptor_bytes(removable, bus))
        self.assertEqual(cards.is_card_device(desc, include_virtual), expected, (removable, bus))

    def test_card_reader_and_usb_stick(self):
        self.check(True, cards.BUS_USB, True)

    def test_builtin_sd_slot_reported_fixed(self):
        """笔记本自带的读卡器常报成不可移动，但总线是 SD/MMC。"""
        self.check(False, cards.BUS_SD, True)
        self.check(False, cards.BUS_MMC, True)

    def test_usb_hard_drive_is_not_a_card(self):
        self.check(False, cards.BUS_USB, False)

    def test_internal_disks_are_not_cards(self):
        for bus in (3, 11, 17):          # ATA、SATA、NVMe
            self.check(False, bus, False)

    def test_virtual_disks_only_for_tests(self):
        self.check(False, cards.BUS_VIRTUAL, False)
        self.check(False, cards.BUS_VIRTUAL, True, include_virtual=True)

    def test_nothing_known(self):
        self.assertFalse(cards.is_card_device(None))


class ThisComputerTest(unittest.TestCase):
    """在真机上跑：系统盘无论如何都不能被当成卡。"""

    def test_system_drive_is_never_a_card(self):
        self.assertFalse(cards.drive_is_card(cards.system_letter()))
        self.assertNotIn(cards.system_letter(), cards.card_letters())

    def test_system_disk_not_listed(self):
        system = {v["disk"] for v in cards.volumes() if v["letter"].upper() == cards.system_letter()}
        self.assertTrue(system, "应该能找到系统盘所在的磁盘")
        self.assertFalse(system & {d["number"] for d in cards.card_disks()})

    def test_volume_serial_and_info(self):
        self.assertIsNotNone(cards.volume_serial(cards.system_letter()))
        self.assertTrue(cards.volume_info(cards.system_letter())[1])      # 文件系统名，像 NTFS

    def test_not_a_drive(self):
        self.assertFalse(cards.drive_is_card("folder"))


class PcReasonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = support.temp_dir(self, "nxcards_")

    def test_plain_folder_is_fine(self):
        self.assertIsNone(cards.pc_reason(str(self.tmp)))

    def test_system_drive_root(self):
        self.assertIn("系统盘", cards.pc_reason(cards.system_letter()))

    def test_folder_with_windows_markers(self):
        for name in ("Windows", "Program Files", "pagefile.sys"):
            with self.subTest(name=name):
                folder = self.tmp / name.replace(" ", "_")
                (folder / name).mkdir(parents=True)
                self.assertIn("电脑硬盘", cards.pc_reason(str(folder)))

    def test_folder_holding_user_profile(self):
        """选到了 C:\\Users 这种层级，里面有用户文件夹。"""
        profile = os.environ.get("USERPROFILE")
        if not profile:
            self.skipTest("没有 USERPROFILE")
        self.assertIsNotNone(cards.pc_reason(str(Path(profile).parent)))

    def test_eject_folder_refused(self):
        ok, message = cards.eject(str(self.tmp))
        self.assertFalse(ok)
        self.assertIn("文件夹", message)


if __name__ == "__main__":
    unittest.main()
