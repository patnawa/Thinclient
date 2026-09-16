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
    return {"schema": 1, "architecture": platform.machine(), "kernel": platform.release(),
            "vendor": read("/sys/class/dmi/id/sys_vendor"),
            "model": read("/sys/class/dmi/id/product_name"),
            "memory": read("/proc/meminfo").splitlines()[0],
            "pci_drivers": pci, "build": read("/etc/thinclient/build-info")}


if __name__ == "__main__":
    print(json.dumps(inventory(), indent=2))
