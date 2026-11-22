"""换包端到端测试：造一张假存储卡和一个假整合包，用真实的工具接口跑一遍"计算 → 清理并安装"，再核对卡上的文件。

用法：python tests\swap\e2e_fake_card.py <工作文件夹> [--build-only]
数据目录（设置、快照）指到工作文件夹里，不碰真实的 %LOCALAPPDATA%\\NXToolbox。
"""
import os
import shutil
import struct
import sys
import time
import zipfile
from pathlib import Path

WORK = Path(sys.argv[1]).resolve()
CARD, PACK, DATA = WORK / "card", WORK / "pack.zip", WORK / "data"
REPO = Path(__file__).resolve().parents[2]

MOD_ID = "1EC9082CB52ECAF561EE45BC00FF318E9912232B"      # 游戏 MOD 的 Build ID
NRO_ID = "8D2A5B1C0F9E7D6C5B4A39281706F5E4D3C2B1A0"
OLD_ES = "C70E4A0C8B0F27C50E9D10F7A6A3E1F4"               # 旧包的签名补丁，新包已经没有这个版本了
SHARED_SYS = "D1D2D3D4D5D6D7D8D9DADBDCDDDEDFE0"           # 旧包放在改了名的文件夹里，新包也有
NEW_ES = "A1A2A3A4A5A6A7A8A9AAABACADAEAFB0"
IPS = b"PATCH" + b"\x00\x01\x00\x00\x01\xAA" + b"EOF"
CHEAT_TOTK = "[60 FPS]\n04000000 01234567 52800000\n"


def pk31(ams, hos):
    head = bytearray(b"PK31" + bytes(60))
    struct.pack_into("<BBBB", head, 0x34, 0, hos[2], hos[1], hos[0])
    struct.pack_into("<BBBB", head, 0x38, 0, ams[2], ams[1], ams[0])
    return bytes(head)


CARD_FILES = {
    # 大气层本体（旧版本）
    "atmosphere/package3": pk31((1, 8, 0), (19, 0, 1)),
    "atmosphere/config/system_settings.ini": "[atmosphere]\n",
    "atmosphere/blacklist.txt": "atmosphere/exefs_patches\n",         # 旧包留下的名单，真实的包就是这么写的
    "atmosphere/erpt_reports/0001.log": "report",
    "atmosphere/crash_reports/0001.log": "crash",
    # contents：游戏的金手指和 MOD
    "atmosphere/contents/0100F2C0115B6000/cheats/0123456789ABCDEF.txt": CHEAT_TOTK,   # 王国之泪 金手指
    "atmosphere/contents/0100F2C0115B6000/cheats/toggles.txt": "[60 FPS]\ntrue\n",
    "atmosphere/contents/01006A800016E000/exefs/subsdk9": "skyline",                   # 大乱斗 Skyline
    "atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libarcropolis.nro": "arc",
    "atmosphere/contents/0100A3D008C5C000/romfs.bin": "romfs image",                  # 单文件 romfs
    "atmosphere/contents/0100CCCC00000000/cheats/toggles.txt": "[x]\ntrue\n",          # 只剩开关记录
    "atmosphere/contents/0100CCCC00000000/romfs_metadata.bin": "cache",
    "atmosphere/contents/0100DDDD00000000/cheats/60fps.txt": CHEAT_TOTK,               # 文件名不对，不会生效
    # contents：系统模块（插件）
    "atmosphere/contents/010000000000bd00/exefs.nsp": "MissionControl",
    "atmosphere/contents/010000000000bd00/flags/boot2.flag": "",
    "atmosphere/contents/4200000000000010/exefs.nsp": "ldn_mitm",
    "atmosphere/contents/4200000000000010/mitm.lst": "ldn:u",
    "atmosphere/contents/0100EEEE00000000/mitm.lst": "set:sys",
    "atmosphere/contents/0100EEEE00000000/exefs/main": "sysmodule",
    "atmosphere/contents/0100000000001000/romfs/lyt/common.szs": "theme",               # 装好的主题
    # exefs_patches / nro_patches / kip_patches
    f"atmosphere/exefs_patches/塞尔达传说 王国之泪 60帧 1.2.1/{MOD_ID}.ips": IPS,
    "atmosphere/exefs_patches/TOTK Res 1080p/totk_121.pchtxt":
        "@nsobid-0F1E2D3C4B5A69788796A5B4C3D2E1F0\n# The Legend of Zelda: TotK 1.2.1 - 1080p\n@enabled\n04A0 1F2003D5\n@stop\n",
    f"atmosphere/exefs_patches/es_patches/{OLD_ES}.ips": IPS,
    f"atmosphere/exefs_patches/nfim_ctest/{OLD_ES[:16]}AA.ips": IPS,
    f"atmosphere/exefs_patches/旧包签名补丁/{SHARED_SYS}.ips": IPS,
    "atmosphere/exefs_patches/空的MOD/log.txt": "log",
    "atmosphere/exefs_patches/假补丁/0123456789ABCDEF.ips": "not ips",
    f"atmosphere/nro_patches/大乱斗 60fps/{NRO_ID}.ips": IPS,
    "atmosphere/kip_patches/fs_patches/0011223344556677.ips": IPS,
    # 大气层的用户数据
    "atmosphere/saves/sysmmc/user/account/0100F2C0115B6000/save.bin": "GAME SAVE",
    "atmosphere/hosts/emummc.txt": "127.0.0.1 *nintendo*\n",
    "atmosphere/automatic_backups/PRODINFO.bin": "prodinfo",
    # 其他
    "bootloader/hekate_ipl.ini": "[config]\n",
    "bootloader/payloads/old.bin": "old",
    "switch/prod.keys": "keys",
    "switch/JKSV.nro": "nro",
    "JKSV/Zelda/2026-11-20.zip": "save zip",
    "switch/DBI/DBI.nro": "dbi",
    "emuMMC/RAW1/raw_based": b"\0\0\0\0",
    "Nintendo/Contents/registered/a.nca": "nca",
}

PACK_FILES = {
    "atmosphere/package3": pk31((1, 9, 0), (20, 0, 0)),
    "atmosphere/config/system_settings.ini": "[atmosphere]\nnew=1\n",
    "atmosphere/blacklist.txt": "atmosphere/exefs_patches\n",
    "atmosphere/contents/010000000000bd00/exefs.nsp": "MissionControl new",
    "atmosphere/contents/010000000000bd00/flags/boot2.flag": "",
    f"atmosphere/exefs_patches/es_patches/{NEW_ES}.ips": IPS,
    f"atmosphere/exefs_patches/sigpatches/{SHARED_SYS}.ips": IPS,
    "atmosphere/kip_patches/fs_patches/8899AABBCCDDEEFF.ips": IPS,
    "atmosphere/hosts/default.txt": "# default\n",
    "bootloader/hekate_ipl.ini": "[config]\nnew=1\n",
    "switch/DBI/DBI.nro": "dbi new",
}

# 换包后每一项应该在不在
EXPECT_AFTER = {
    "atmosphere/contents/0100F2C0115B6000/cheats/0123456789ABCDEF.txt": True,
    "atmosphere/contents/01006A800016E000/exefs/subsdk9": True,
    "atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libarcropolis.nro": True,
    "atmosphere/contents/0100A3D008C5C000/romfs.bin": True,
    "atmosphere/contents/0100CCCC00000000": False,
    "atmosphere/contents/0100DDDD00000000": False,
    "atmosphere/contents/4200000000000010": False,
    "atmosphere/contents/0100EEEE00000000": False,
    "atmosphere/contents/0100000000001000": False,
    f"atmosphere/exefs_patches/塞尔达传说 王国之泪 60帧 1.2.1/{MOD_ID}.ips": True,
    "atmosphere/exefs_patches/TOTK Res 1080p/totk_121.pchtxt": True,
    f"atmosphere/exefs_patches/es_patches/{OLD_ES}.ips": False,
    f"atmosphere/exefs_patches/es_patches/{NEW_ES}.ips": True,
    "atmosphere/exefs_patches/nfim_ctest": False,
    "atmosphere/exefs_patches/旧包签名补丁": False,
    f"atmosphere/exefs_patches/sigpatches/{SHARED_SYS}.ips": True,
    "atmosphere/exefs_patches/空的MOD": False,
    "atmosphere/exefs_patches/假补丁": False,
    f"atmosphere/nro_patches/大乱斗 60fps/{NRO_ID}.ips": True,
    "atmosphere/kip_patches/fs_patches/0011223344556677.ips": False,
    "atmosphere/kip_patches/fs_patches/8899AABBCCDDEEFF.ips": True,
    "atmosphere/saves/sysmmc/user/account/0100F2C0115B6000/save.bin": True,
    "atmosphere/hosts/emummc.txt": True,
    "atmosphere/hosts/default.txt": True,
    "atmosphere/automatic_backups/PRODINFO.bin": True,
    "atmosphere/erpt_reports": False,
    "atmosphere/crash_reports": False,
    "bootloader/payloads/old.bin": False,
    "switch/prod.keys": True,
    "JKSV/Zelda/2026-11-20.zip": True,
    "emuMMC/RAW1/raw_based": True,
    "Nintendo/Contents/registered/a.nca": True,
}


def write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


def build():
    shutil.rmtree(WORK, ignore_errors=True)
    for rel, data in CARD_FILES.items():
        write(CARD, rel, data)
    with zipfile.ZipFile(PACK, "w") as z:
        for rel, data in PACK_FILES.items():
            z.writestr(rel, data.encode("utf-8") if isinstance(data, str) else data)
    DATA.mkdir(parents=True, exist_ok=True)
    print(f"假卡：{CARD}\n假整合包：{PACK}")


def run():
    os.environ["LOCALAPPDATA"] = str(DATA)
    os.environ["NXTOOLBOX_FAKE_SD"] = str(CARD)
    sys.path.insert(0, str(REPO))
    import host
    from nxhost.api import Context
    tool = next(t for t in host.read_tools() if t["id"] == "swap")
    api = host.load_tool_api(tool, Context(base=host.BASE, frozen=False, process_t0=time.perf_counter(), tool=tool))

    cards = api.cards()
    assert [c["path"] for c in cards] == [str(CARD)], cards
    print(f"认卡：大气层 {cards[0]['ams']}，系统 {cards[0]['hos']}")
    src = api.use_source("local", str(PACK))
    assert not src.get("error"), src
    print(f"整合包：大气层 {src['ams']}，{src['files']} 个文件")

    plan = api.plan(str(CARD))
    print("\n===== 计划 =====")
    for g in plan["groups"]:
        print(f"[{g['name']}] {g['text']}")
        for item in g["items"]:
            print(f"    {'留' if item['keep'] else '清'}  {item['path']}  —  {item['text']}{'  （' + item['hint'] + '）' if item['hint'] else ''}")
    print(f"合计：清掉 {plan['totals']['delete']} 个文件，装入 {plan['totals']['install']} 个")

    # 实时刷新：卡上加了文件，指纹要变
    s1 = api.card_stamp(str(CARD))["stamp"]
    write(CARD, "firmware 20.0.0/a.nca", "nca")
    s2 = api.card_stamp(str(CARD))["stamp"]
    shutil.rmtree(CARD / "firmware 20.0.0")
    s3 = api.card_stamp(str(CARD))["stamp"]
    print(f"\n指纹：加文件后{'变了' if s1 != s2 else '没变（不对）'}，删掉后{'恢复' if s1 == s3 else '没恢复（不对）'}")

    plan = api.plan(str(CARD))
    r = api.start(str(CARD), plan["totals"]["delete"])
    assert r.get("ok"), r
    while api.progress().get("running"):
        time.sleep(0.1)
    state = api.progress()
    assert not state.get("error"), state
    print(f"\n===== 执行 =====\n清掉 {state['result']['deleted']}，装入 {state['result']['installed']}，"
          f"大气层现在 {state['result']['card']['ams']}")

    print("\n===== 核对卡上的文件 =====")
    bad = 0
    for rel, want in EXPECT_AFTER.items():
        got = (CARD / rel).exists()
        ok = got == want
        bad += not ok
        print(f"{'OK ' if ok else '错 '} {'应在' if want else '应无'}  {rel}")
    print(f"\n{'全部符合' if not bad else f'{bad} 项不符合'}")
    snaps = api.snapshots()
    print(f"快照：{len(snaps['items'])} 个，{snaps['items'][0]['files'] if snaps['items'] else 0} 个文件")
    return bad


if __name__ == "__main__":
    build()
    if "--build-only" not in sys.argv:
        sys.exit(1 if run() else 0)
