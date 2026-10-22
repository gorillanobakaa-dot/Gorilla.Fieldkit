# Version: 1.0.0 · updated 26-10-22-17-00
"""fieldkit firmware check: the ALFA AWUS036NH of 2026-10-22 (RT3070, rt2800usb built in, rt2870.bin missing) is
found whether it is attached at the moment or has reset off the bus; a driver that lists many files and loaded one
is not a finding. The log lines are copied from the VAIO's dmesg that day."""
import os
import sys

import pytest

from fieldkit.firmware import fwcheck as F

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux-only tool: a Linux kernel's /sys and firmware (the fake /sys paths hold colons Windows refuses)")

LOG = """[ 8177.726677] usb 4-1.5: new high-speed USB device number 22 using ehci-pci
[ 8177.823212] usb 4-1.5: New USB device found, idVendor=148f, idProduct=3070, bcdDevice= 1.01
[ 8177.823230] usb 4-1.5: Product: 802.11 n WLAN
[ 8177.823231] usb 4-1.5: Manufacturer: Ralink
[ 8178.073768] ieee80211 phy16: rt2x00lib_request_firmware: Info - Loading firmware file 'rt2870.bin'
[ 8178.073793] rt2800usb 4-1.5:1.0: Direct firmware load for rt2870.bin failed with error -2
[ 8180.141979] rt2800usb 4-1.5:1.0: Direct firmware load for rt2870.bin failed with error -2
"""
MODINFO = b"rt2800usb.firmware=rt2870.bin\0rt2800usb.license=GPL\0r8169.firmware=rtl_nic/rtl8168d-1.fw\0" \
          b"r8169.firmware=rtl_nic/rtl8168g-2.fw\0"


def sysfs_with(tmp, attached):
    """A /sys with the r8169 card (PCI) and, if attached, the ALFA's USB interface."""
    s = tmp / "sys"
    (s / "module" / "rt2800usb").mkdir(parents=True)
    (s / "module" / "r8169").mkdir(parents=True)
    for bus, drv, mod in (("usb", "rt2800usb", "rt2800usb"), ("pci", "r8169", "r8169")):
        d = s / "bus" / bus / "drivers" / drv
        d.mkdir(parents=True)
        (d / "module").symlink_to(s / "module" / mod)
        (s / "bus" / bus / "devices").mkdir(parents=True, exist_ok=True)
    pci = s / "devices" / "pci0000:00" / "0000:03:00.0"
    pci.mkdir(parents=True)
    (pci / "vendor").write_text("0x10ec\n")
    (pci / "device").write_text("0x8168\n")
    (pci / "driver").symlink_to(s / "bus" / "pci" / "drivers" / "r8169")
    (s / "bus" / "pci" / "devices" / "0000:03:00.0").symlink_to(pci)
    if attached:
        usb = s / "devices" / "usb4" / "4-1" / "4-1.5"
        (usb / "4-1.5:1.0").mkdir(parents=True)
        (usb / "idVendor").write_text("148f\n")
        (usb / "idProduct").write_text("3070\n")
        (usb / "manufacturer").write_text("Ralink\n")
        (usb / "product").write_text("802.11 n WLAN\n")
        (usb / "4-1.5:1.0" / "driver").symlink_to(s / "bus" / "usb" / "drivers" / "rt2800usb")
        (s / "bus" / "usb" / "devices" / "4-1.5:1.0").symlink_to(usb / "4-1.5:1.0")
    return s


def run(tmp, attached, log=LOG, config='CONFIG_EXTRA_FIRMWARE=""\n', files=()):
    tmp.mkdir(exist_ok=True)
    mi = tmp / "modules.builtin.modinfo"
    mi.write_bytes(MODINFO)
    fw = tmp / "firmware"
    (fw / "rtl_nic").mkdir(parents=True, exist_ok=True)
    (fw / "rtl_nic" / "rtl8168g-2.fw").write_bytes(b"x")      # r8169 loaded this one; rtl8168d-1.fw is absent
    for f in files:
        (fw / f).write_bytes(b"x")
    return F.check(sysfs=str(sysfs_with(tmp, attached)), release="test", modinfo_path=str(mi), fw_dirs=(str(fw),),
                   log_text=log, config_text=config)


def test_alfa_attached_is_found_with_the_kernel_fix(tmp_path):
    findings, info = run(tmp_path, attached=True)
    assert len(findings) == 1
    f = findings[0]
    assert (f["id"], f["driver"], f["driver_kind"], f["firmware"]) == ("148f:3070", "rt2800usb", "built-in", "rt2870.bin")
    assert f["device"] == "Ralink 802.11 n WLAN" and "2 time(s)" in f["kernel_said"]
    assert f["fix"].startswith("the driver is built into the kernel: BAKE IT IN")
    assert 'CONFIG_EXTRA_FIRMWARE="rt2870.bin"' in f["fix"] and "/lib/firmware" not in f["fix"]   # owner: BAKED IN


def test_alfa_that_reset_off_the_bus_is_still_found(tmp_path):
    findings, _ = run(tmp_path, attached=False)
    assert len(findings) == 1
    f = findings[0]
    assert f["id"] == "148f:3070" and f["device"] == "Ralink 802.11 n WLAN"
    assert "not attached now" in f["kernel_said"] and f["driver_kind"] == "built-in"


def test_many_declared_one_loaded_is_noted_not_found(tmp_path):
    findings, info = run(tmp_path, attached=True, log="")
    assert findings == []
    assert any(i.startswith("r8169: 1 of 2") for i in info)


def test_built_in_or_on_disk_firmware_clears_it(tmp_path):
    findings, info = run(tmp_path / "a", attached=True, log="", config='CONFIG_EXTRA_FIRMWARE="rt2870.bin"\n')
    assert findings == [] and not any(i.startswith("rt2800usb") for i in info)
    _, info = run(tmp_path / "c", attached=True, log="")                  # not built in, not on disk, no log line
    assert any(i.startswith("rt2800usb (built-in") and "none of its declared firmware" in i for i in info)
    findings, _ = run(tmp_path / "b", attached=True, files=("rt2870.bin",))
    assert len(findings) == 1 and "loads until then" in findings[0]["fix"] and "BAKE IT IN" in findings[0]["fix"]


def test_compressed_firmware_counts(tmp_path):
    (tmp_path / "rt2870.bin.zst").write_bytes(b"x")
    assert F.on_disk("rt2870.bin", (str(tmp_path),)) == os.path.join(str(tmp_path), "rt2870.bin.zst")


def test_builtin_modinfo_is_read_per_module(tmp_path):
    p = tmp_path / "m"
    p.write_bytes(MODINFO)
    assert F.builtin_firmware(str(p)) == {"rt2800usb": ["rt2870.bin"],
                                          "r8169": ["rtl_nic/rtl8168d-1.fw", "rtl_nic/rtl8168g-2.fw"]}
