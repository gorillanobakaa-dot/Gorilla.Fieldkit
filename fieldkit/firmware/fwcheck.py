#!/usr/bin/env python3
# Version: 1.1.0 · updated 26-10-22-17-20
"""fwcheck.py - does every device plugged into this Linux machine have the firmware its driver needs.

    fieldkit firmware check [--json]     read-only; exit 0 fine, 3 findings
    fieldkit firmware doctor             the programs and files this tool reads

Born 2026-10-22. The owner plugged in the ALFA AWUS036NH antenna (Ralink RT3070) and it never came up: the custom
kernel has the driver built in (CONFIG_RT2800USB=y) but not its firmware (CONFIG_EXTRA_FIRMWARE=""), and the file
was not on disk either. The kernel said so in one line ("Direct firmware load for rt2870.bin failed with error -2")
that nobody read, and the kernel project's own auditor called the empty setting "correct".

HOW IT DECIDES
  1. Present hardware: every USB and PCI device in /sys with a driver bound, and the module behind that driver
     (/sys/bus/*/drivers/<driver>/module, which exists for built-in drivers too).
  2. What each driver needs: a built-in driver's firmware is listed in
     /lib/modules/<kernel>/modules.builtin.modinfo ("<module>.firmware=<file>"); a loadable one through
     modinfo -F firmware.
  3. Where firmware can be: /lib/firmware/updates, /lib/firmware (plain, .xz or .zst), firmware_class.path, and
     inside the kernel image (CONFIG_EXTRA_FIRMWARE in the kernel's config).
  4. What happened: the kernel log's "Direct firmware load for <file> failed" lines.

  FINDING  a file the kernel FAILED to load for a device that is plugged in. That device does not work.
  noted    a declared file that is absent while the driver loaded another one or never asked: drivers list every
           file they could use (Intel Wi-Fi lists dozens of versions) and need one, so absent is not broken.
  Owner's rule, 2026-10-22: firmware "must be BAKED IN. We build monolithic kernels mostly." For a built-in driver
  the only fix named is the kernel's CONFIG_EXTRA_FIRMWARE at the next build; this tool never installs or changes
  anything.
"""
import argparse
import glob
import gzip
import json
import os
import re
import shutil
import subprocess
import sys

FW_DIRS = ("/lib/firmware/updates", "/lib/firmware", "/usr/lib/firmware/updates", "/usr/lib/firmware")
FAILED = re.compile(r"(\S+) (\S+): Direct firmware load for (\S+) failed with error (-?\d+)")


def _read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def present_devices(sysfs="/sys"):
    """[{bus, device, driver, module, name}] for every USB and PCI device with a driver bound."""
    out = []
    for bus in ("usb", "pci"):
        for dev in sorted(glob.glob(os.path.join(sysfs, "bus", bus, "devices", "*"))):
            drv_link = os.path.join(dev, "driver")
            if not os.path.islink(drv_link):
                continue
            driver = os.path.basename(os.readlink(drv_link))
            if driver in ("usb", "hub", "usbhid", "pcieport"):
                continue
            mod_link = os.path.join(sysfs, "bus", bus, "drivers", driver, "module")
            module = os.path.basename(os.readlink(mod_link)) if os.path.islink(mod_link) else driver.replace("-", "_")
            # a USB interface (2-1.3:1.0) carries no ids; its parent folder is the device that does
            base = os.path.dirname(os.path.realpath(dev)) if bus == "usb" and ":" in os.path.basename(dev) else dev
            vid = (_read(os.path.join(base, "idVendor")) or _read(os.path.join(base, "vendor"))).strip().replace("0x", "")
            pid = (_read(os.path.join(base, "idProduct")) or _read(os.path.join(base, "device"))).strip().replace("0x", "")
            name = (_read(os.path.join(base, "manufacturer")).strip() + " " + _read(os.path.join(base, "product")).strip()).strip()
            out.append({"bus": bus, "device": os.path.basename(dev), "driver": driver, "module": module,
                        "id": f"{vid}:{pid}" if vid else "", "name": name})
    return out


def builtin_firmware(modinfo_path):
    """module -> [firmware files] declared by the drivers built into the kernel."""
    out = {}
    try:
        with open(modinfo_path, "rb") as f:
            data = f.read().decode(errors="replace")
    except OSError:
        return out
    for rec in data.split("\0"):
        m = re.fullmatch(r"([^.=]+)\.firmware=(.+)", rec)
        if m:
            out.setdefault(m.group(1), []).append(m.group(2))
    return out


def module_firmware(module, builtin):
    if module in builtin:
        return builtin[module], "built-in"
    exe = shutil.which("modinfo") or ("/usr/sbin/modinfo" if os.path.exists("/usr/sbin/modinfo") else None)
    if not exe:
        return [], "unknown"
    try:
        r = subprocess.run([exe, "-F", "firmware", module], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return [], "unknown"
    return [l.strip() for l in r.stdout.splitlines() if l.strip()], "module" if r.returncode == 0 else "unknown"


def kernel_config(release):
    for p in (f"/boot/config-{release}",):
        if os.path.exists(p):
            return _read(p)
    try:
        with gzip.open("/proc/config.gz", "rt", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def extra_firmware(config_text):
    m = re.search(r'^CONFIG_EXTRA_FIRMWARE="([^"]*)"', config_text, re.M)
    return set(m.group(1).split()) if m else set()


def on_disk(fw, dirs):
    for d in dirs:
        for suffix in ("", ".xz", ".zst"):
            if os.path.isfile(os.path.join(d, fw + suffix)):
                return os.path.join(d, fw + suffix)
    return None


def kernel_log():
    for argv in (["dmesg"], ["journalctl", "-k", "-b", "--no-pager", "-o", "cat"]):
        exe = shutil.which(argv[0]) or (f"/usr/bin/{argv[0]}" if os.path.exists(f"/usr/bin/{argv[0]}") else None)
        if not exe:
            continue
        try:
            r = subprocess.run([exe] + argv[1:], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout, argv[0]
    return "", None


def failed_loads(log_text):
    """{(driver, firmware): {"device", "error", "count"}} from the kernel log."""
    out = {}
    for m in FAILED.finditer(log_text):
        key = (m.group(1), m.group(3))
        d = out.setdefault(key, {"device": m.group(2), "error": m.group(4), "count": 0})
        d["count"] += 1
    return out


USB_NEW = re.compile(r"usb (\S+): New USB device found, idVendor=(\w+), idProduct=(\w+)")
USB_STR = re.compile(r"usb (\S+): (Product|Manufacturer): (.+)")


def usb_seen(log_text):
    """USB port -> {id, name} from the kernel log, for a device that has gone again (it resets after a failed
    firmware load, so it is often not in /sys at the moment of the check)."""
    out = {}
    for m in USB_NEW.finditer(log_text):
        out.setdefault(m.group(1), {})["id"] = f"{m.group(2)}:{m.group(3)}"
    for m in USB_STR.finditer(log_text):
        d = out.setdefault(m.group(1), {})
        d[m.group(2).lower()] = m.group(3).strip()
    return {k: {"id": v.get("id", ""), "name": (v.get("manufacturer", "") + " " + v.get("product", "")).strip()}
            for k, v in out.items()}


def fix_text(fw, kind, where):
    """The fix for one failed firmware. Owner's rule (2026-10-22): "must be BAKED IN. We build monolithic kernels
    mostly." For a built-in driver the firmware goes INTO the kernel image (CONFIG_EXTRA_FIRMWARE); /lib/firmware is
    never offered as the fix for it, only named as what makes it load until the next build."""
    bake = (f"BAKE IT IN at the next kernel build: CONFIG_EXTRA_FIRMWARE=\"{fw}\" (with CONFIG_EXTRA_FIRMWARE_DIR "
            f"pointing at a folder holding {fw} from linux-firmware, checksum recorded)")
    if kind == "built-in":
        return (f"the file is on disk now, so it loads until then; the driver is built into the kernel: {bake}"
                if where else f"the driver is built into the kernel: {bake}")
    if where:
        return "the file is there NOW: plug the device in again"
    return (f"put {fw} in /lib/firmware (linux-firmware) and plug the device in again; on a Gorilla monolithic kernel "
            f"build the driver in (=y) and {bake[0].lower() + bake[1:]}")


def check(sysfs="/sys", release=None, modinfo_path=None, fw_dirs=None, log_text=None, config_text=None,
          module_fw=None):
    release = release or os.uname().release
    modinfo_path = modinfo_path or f"/lib/modules/{release}/modules.builtin.modinfo"
    extra_path = _read("/sys/module/firmware_class/parameters/path").strip()
    fw_dirs = fw_dirs or tuple(d for d in ((extra_path,) if extra_path else ()) + FW_DIRS)
    builtin = builtin_firmware(modinfo_path)
    if log_text is None:
        log_text, log_src = kernel_log()
    else:
        log_src = "given"
    config_text = kernel_config(release) if config_text is None else config_text
    baked = extra_firmware(config_text)
    fails = failed_loads(log_text)
    findings, info, seen = [], [], set()
    if not log_src:
        info.append("the kernel log could not be read (dmesg needs root on this system): failed loads are not "
                    "checked, only what is on disk")
    for dev in present_devices(sysfs):
        if dev["module"] in seen:
            continue
        seen.add(dev["module"])
        fws, kind = (module_fw or module_firmware)(dev["module"], builtin)
        for (drv, fw), f in sorted(fails.items()):
            if drv not in (dev["driver"], dev["module"]):
                continue
            where = on_disk(fw, fw_dirs)
            fix = fix_text(fw, kind, where)
            findings.append({"device": dev["name"] or dev["id"] or dev["device"], "id": dev["id"],
                             "driver": dev["driver"], "driver_kind": kind, "firmware": fw,
                             "kernel_said": f"Direct firmware load for {fw} failed with error {f['error']} "
                                            f"({f['count']} time(s))",
                             "on_disk_now": where, "built_in": fw in baked,
                             "fix": fix})
        absent = [fw for fw in fws if fw not in baked and not on_disk(fw, fw_dirs)
                  and not any(k[1] == fw for k in fails)]
        if fws and absent and len(absent) < len(fws):
            info.append(f"{dev['driver']}: {len(absent)} of {len(fws)} declared firmware files absent; the driver "
                        f"uses one of them, so this is noted, not a finding")
        elif fws and absent:
            info.append(f"{dev['driver']} ({kind}, {dev['name'] or dev['id']}): none of its declared firmware "
                        f"({', '.join(absent[:4])}{' ...' if len(absent) > 4 else ''}) is on disk or built in, and "
                        f"the kernel log has no failed load for it; check the device works")
    # failed loads for devices not bound now: a USB stick that resets after the failure drops off the bus, and
    # that is exactly the device that does not work (the ALFA, 2026-10-22, was absent from lsusb between resets)
    reported = {(f["driver"], f["firmware"]) for f in findings}
    seen_usb = usb_seen(log_text)
    for (drv, fw), f in sorted(fails.items()):
        if (drv, fw) in reported:
            continue
        port = f["device"].split(":")[0]
        dev = seen_usb.get(port, {})
        mod_link = os.path.join(sysfs, "bus", "usb", "drivers", drv, "module")
        module = os.path.basename(os.readlink(mod_link)) if os.path.islink(mod_link) else drv
        kind = "built-in" if module in builtin else "module"
        where = on_disk(fw, fw_dirs)
        findings.append({"device": dev.get("name") or dev.get("id") or f["device"], "id": dev.get("id", ""),
                         "driver": drv, "driver_kind": kind, "firmware": fw,
                         "kernel_said": f"Direct firmware load for {fw} failed with error {f['error']} "
                                        f"({f['count']} time(s)); the device is not attached now (it resets after "
                                        f"a failed load, or was unplugged)",
                         "on_disk_now": where, "built_in": fw in baked,
                         "fix": fix_text(fw, kind, where)})
    return findings, info


def doctor():
    rel = os.uname().release
    rows = [("modules.builtin.modinfo", os.path.exists(f"/lib/modules/{rel}/modules.builtin.modinfo"),
             "the firmware built-in drivers declare (built-in drivers are not checked without it)"),
            ("modinfo", bool(shutil.which("modinfo") or os.path.exists("/usr/sbin/modinfo")),
             "the firmware loadable modules declare (kmod)"),
            ("kernel config", bool(kernel_config(rel)), "CONFIG_EXTRA_FIRMWARE: firmware inside the kernel image"),
            ("kernel log", bool(kernel_log()[1]), "failed loads (dmesg or journalctl -k)")]
    for name, ok, why in rows:
        print(f"{'ok     ' if ok else 'MISSING'} {name}: {why}")
    return 0 if all(r[1] for r in rows[:1]) else 1


def main(argv=None, prog="fwcheck.py"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0],
                                 epilog="\n".join(__doc__.split("\n\n")[1].splitlines()),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("check", help="devices plugged in whose firmware failed to load (read-only; exit 3 = findings)")
    c.add_argument("--json", action="store_true")
    sp.add_parser("doctor", help="the files and programs this tool reads")
    a = ap.parse_args(argv)
    if not sys.platform.startswith("linux"):
        print("not-this-platform: Linux device firmware is checked on Linux only")
        return 0
    if a.cmd == "doctor":
        return doctor()
    findings, info = check()
    if a.json:
        print(json.dumps({"findings": findings, "info": info}, indent=1))
    else:
        for line in info:
            print("  " + line)
        for f in findings:
            print(f"FINDING {f['device']} ({f['id']}, driver {f['driver']}, {f['driver_kind']})\n"
                  f"  kernel said: {f['kernel_said']}\n  fix: {f['fix']}")
        print(f"{len(findings)} finding(s)" if findings else "CLEAN: every plugged-in device's firmware loaded")
    return 3 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
