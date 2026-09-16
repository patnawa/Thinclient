# Hardware and performance validation

1.5.1 is a development candidate. Do not replace a live image merely because it
builds or passes a VM test. Keep immutable current/previous trees and use a
non-critical, explicitly selected physical client before fleet promotion.

## Fleet target and test priority

User-specified target: Dell and HP PCs with 2–4 GB RAM, Gen3–Gen10. For planning,
interpret the generations as Intel Core CPU generations, not Dell/HP product
generation numbers. Exact model names, NICs and storage controllers are still
unknown; record them from each machine rather than inferring them from its age.

Prioritize physical testing in this order, including both vendors:

1. Oldest available Gen3–Gen4 machines with 2 GB RAM: Lite ISO/PXE boot,
   responsive UI, a real RDP session and peak memory usage.
2. Newest available Gen8–Gen10 machines with 4 GB RAM: Lite and Full boot,
   firmware/Secure Boot where available, actual NIC and storage discovery.
3. Gen5–Gen7 representatives and remaining 2 GB/4 GB configurations: repeat the
   same checks and cover differences in graphics, NICs, storage and peripherals.

Use Lite as the first 2 GB test candidate. Test Full where its extra features
are required, including at 2 GB if that is an actual deployment requirement;
do not assume 4 GB is mandatory or that every 2 GB workload will fit. Compare
boot time and memory under the same real RDP workload, including required audio,
displays and peripherals, and check for OOM events or reconnect/UI failures.

Record SATA, NVMe, eMMC, NIC vendor/device IDs and controller mode only as found.
Test installation only on an approved spare disk and boot it again with the ISO
removed. Do not change storage-controller or firmware security settings on an
in-use PC merely to make a candidate pass. This fleet target is a test plan,
not a claim of Gen3–Gen10 physical compatibility.

## Local image gates

Run both normal release gates first, then the supplemental matrix sequentially:

```bash
RELEASE_VERSION=1.5.1 RELEASE_OUT="$PWD/out/candidate-1.5.1" bash build/verify-release.sh
TC_CONFIG_LOCAL=/dev/null DISTRO_VERSION=1.5.1-lite IMAGE_NAME=thinclient-lite-amd64 \
  OUTDIR="$PWD/out/candidate-1.5.1/lite" bash build/hardware-matrix.sh
```

The matrix covers a baseline x86-64 CPU, 2 GiB RAM, standard/virtio graphics,
Intel/Realtek/virtio emulated NICs, BIOS SATA installation, UEFI NVMe installation,
and installed-disk Secure Boot. `MATRIX_EXPERIMENTAL_1G=1` adds an exploratory
1 GiB PXE test; it is not a claim that a remote desktop session fits 1 GiB.
Choose a new `MATRIX_OUT` for each run. Tests use disposable VM disks and a
private PXE copy. Existing QEMU helpers have shared monitor names; do not run
multiple matrices or release gates concurrently on one host.

Individual tests accept `TC_TEST_RAM_MB`, `TC_TEST_CPUS`, `TC_TEST_CPU`,
`TC_TEST_NIC` and `TC_TEST_VGA`. Installer tests additionally accept
`TC_TEST_DISK_BUS=virtio|sata|nvme`; PXE NIC choices are `e1000`, `rtl8139`, and
`virtio-net-pci`. These emulate controller families, not actual physical models.

`boot-timing.json` measures VM launch to the first UI-ready message, including
firmware/PXE-loader time. `kernel_to_ui_seconds` preserves the previous uptime
measurement. Neither measures physical power-on or successful RDP authentication.
Record those separately with a timestamped external observation. Use repeated
samples with identical RAM, CPU, firmware and network conditions for comparisons.

## Physical checklist

Use `tc-diag --json` to record model, PCI drivers, USB devices, storage bus, RAM,
firmware and Secure Boot state. Do not collect serial numbers, session credentials
or customer desktop screenshots. For each exact model, record:

- BIOS/UEFI/Secure Boot ISO, PXE and installed-disk boot outcomes separately.
- SATA/NVMe/VMD/eMMC discovery, unplugged installation media, and cold power-on.
- Intel/Realtek/USB Ethernet, Wi-Fi where included, DHCP, cable loss and recovery.
- Older integrated graphics, 1024×768, dual monitors, keyboard-only operation,
  audio, and required printers/smart cards/USB devices during a real session.
- No cache USB, slow USB, corrupt/full/removed cache, repeated warm/cold boots.
- Peak memory during boot and RDP; do not infer a RAM minimum from idle UI alone.

Lite now explicitly includes MMC block and SDHCI drivers. Module presence is a
structural gate, not evidence that any particular eMMC device has been tested.
The cache skips discovery retry sleeps only when udev has settled and no USB
storage interface is present. `tc.cache.wait=1` restores the bounded legacy wait
for unusual controller firmware; Network Only remains the recovery menu entry.

## Checksum-verified load measurements

Start a candidate server on loopback using a separate port and history file:

```bash
python3 tools/tc-config-server.py --root out/candidate-1.5.1/full/pxe \
  --bind 127.0.0.1 --port 18081 --max-workers 128 \
  --state-file out/benchmark-status.json
```

In a second terminal:

```bash
python3 tools/pxe-benchmark.py http://127.0.0.1:18081/thinclient/filesystem.squashfs \
  --artifact out/candidate-1.5.1/full/pxe/thinclient/filesystem.squashfs \
  --clients 1,10,50 --output out/benchmark.json
```

Every stream is hashed against the local trusted file, including size checks.
The tool reports total throughput and per-client median/p95 time, fails on any
bad transfer, bounds concurrency and transfer time, and never follows redirects.
Remote URLs require `--allow-remote`: use only during an approved maintenance
window, never as an automatic release step against a live fleet.

For a static-server comparison, expose only the same immutable artifact through
an isolated Nginx listener on another loopback port, enable `sendfile`, and run
the identical command against it. Keep configuration/status handling and their
security rules separate. A static-file benchmark does not justify replacing the
production server or dropping its observability. Repeat runs after warm-up;
hashing clients, filesystem caches and other VM tests can limit the benchmark.

On a shared 1 Gb/s uplink, 50 uncached filesystem images already imply minutes
of aggregate transmission. Evaluate prepared cache USBs and staggered starts
before attributing slow cold-fleet boots to server CPU. Preserve checksum
verification when optimizing copies or cache preparation.
