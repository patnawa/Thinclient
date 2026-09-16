"""Export driver-oriented hardware evidence without credentials or addresses."""
import json
import platform
from pathlib import Path
import subprocess


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return "unknown"


def inventory():
    try:
        pci = subprocess.run(["lspci", "-Dnnk"], text=True, capture_output=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        pci = "unavailable"
    try:
        storage = json.loads(subprocess.run(
            ["lsblk", "-J", "-b", "-d", "-o", "NAME,SIZE,MODEL,TRAN,ROTA,TYPE"],
            text=True, capture_output=True, timeout=5).stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        storage = {"blockdevices": []}
    try:
        usb = subprocess.run(["lsusb"], text=True, capture_output=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        usb = "unavailable"
    memory = read("/proc/meminfo").splitlines()
    firmware = "uefi" if Path("/sys/firmware/efi").exists() else "bios"
    secure_boot = "unknown" if firmware == "uefi" else "not-applicable"
    for variable in Path("/sys/firmware/efi/efivars").glob("SecureBoot-*"):
        try:
            value = variable.read_bytes()
            if len(value) == 5:
                secure_boot = "enabled" if value[4] else "disabled"
        except OSError:
            pass
    return {"schema": 1, "architecture": platform.machine(), "kernel": platform.release(),
            "vendor": read("/sys/class/dmi/id/sys_vendor"),
            "model": read("/sys/class/dmi/id/product_name"),
            "memory": memory[0] if memory else "unknown",
            "firmware": firmware, "secure_boot": secure_boot,
            "storage": storage, "usb_devices": usb,
            "pci_drivers": pci, "build": read("/etc/thinclient/build-info")}


if __name__ == "__main__":
    print(json.dumps(inventory(), indent=2))
