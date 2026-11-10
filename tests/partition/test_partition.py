"""换卡：认卡改用共用的 nxhost.cards 之后，换卡这边的接口和读写部分还在、还对。

真正读写磁盘的测试要管理员身份和虚拟磁盘，不在这里跑。
"""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import support  # noqa: E402

api = support.load_tool("partition")
from core import clone, disk, fat32, layout  # noqa: E402  （要在加载工具之后，core 才是换卡的）
from nxhost import cards  # noqa: E402

MB = 0x800


class DiskModuleTest(unittest.TestCase):
    def test_lists_same_disks_as_shared_module(self):
        self.assertEqual([d["number"] for d in api.cards()], [d["number"] for d in cards.card_disks()])

    def test_shared_names_still_reachable(self):
        for name in ("volumes", "volume_serial", "disk_path", "BUS_VIRTUAL", "list_disks", "open_disk", "locked",
                     "rescan", "wait_for_volume", "DiskError"):
            self.assertTrue(hasattr(disk, name), name)
        self.assertEqual(disk.disk_path(3), "\\\\.\\PhysicalDrive3")

    def test_no_card_found_for_missing_disk(self):
        self.assertIn("error", api.inspect(999))


class LayoutOnImageTest(unittest.TestCase):
    """在一个稀疏的镜像文件上建 FAT32，再让换卡认出来，确认读写部分没被改坏。"""

    def test_format_and_scan(self):
        folder = support.temp_dir(self, "nxpart_")
        path = folder / "card.img"
        total = 1024 * MB
        open(path, "wb").close()
        subprocess.run(["fsutil", "sparse", "setflag", str(path)], capture_output=True, check=True)   # 稀疏文件，不真的占磁盘
        with open(path, "r+b") as f:
            f.truncate(total * 512)
        device = clone.FileDevice(str(path), writable=True)
        try:
            fat = layout.Partition(0x8000, total - 0x8000 - MB, layout.TYPE_FAT32, mbr_slot=0)
            device.write_at(0, layout.mbr_bytes([fat]))
            info = fat32.format_partition(device, fat.start, fat.size)
            self.assertIn("serial", info)
            card = layout.scan(device)
            self.assertEqual(card.fat.start, 0x8000)
            self.assertEqual(card.extras, [])
            plan = layout.plan(2048 * MB, card)
            self.assertEqual(plan.fat.start, 0x8000)
        finally:
            device.close()


if __name__ == "__main__":
    unittest.main()
