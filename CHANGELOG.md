# ThinClient changelog

## 1.4 — 2026-08-14

### Easier operation

- Made each workspace card a single-click target and grouped connections by
  purpose while keeping raw server addresses off the main screen.
- Added a clear online/cache status banner, staged connection progress, a
  cancellable five-second auto-connect countdown, and actionable error choices.
- Moved Settings, writable network controls, and Terminal into one protected
  Administrator area; split Settings into Basic and Advanced pages.
- Added a public offline Help screen with version, image profile, device/IP,
  cache state, last error, copyable support report, QR support code, network
  test, and this in-program changelog.
- Increased text and control sizes, strengthened keyboard focus and contrast,
  disabled motion, and verified layouts at 1280×800 and 1024×768.

### Faster diskless boot

- Published separate Lite and Full Drivers images. Lite is the wired default
  for older PCs; Full retains Wi-Fi, uncommon firmware, and support tools.
- Made HTTP the default UEFI kernel/initrd path, with an automatic matching
  TFTP recovery entry. Legacy BIOS/PXELINUX support remains available.
- Added checksum-addressed removable USB root caching with visible save
  progress, atomic publication, and safe fallback to HTTP.
- Kept OPNsense as the sole DHCP authority; the Debian Docker service provides
  TFTP and HTTP and supports concurrent fleet booting.

### Reliability and security

- Added bounded DNS/TCP preflight stages and safe cancellation of an in-flight
  FreeRDP process, including the process-creation race window.
- Hardened cache and runtime status writes against hostile symlinks and
  preserved the kiosk-writable, sticky runtime directory permissions.
- Fixed UEFI dual-menu ordering, TFTP-first compatibility mapping, build-output
  configuration checks, slow-image shutdown test timing, and visible Admin
  error handling.
- Expanded automated source, GTK, cache, permission, BIOS/UEFI PXE, and
  shutdown regression coverage.

## 1.3

- Established the Debian 13 diskless thin-client appliance, RDP/RemoteApp/VNC
  manager, hybrid BIOS/UEFI image, central configuration, installer, and
  Docker-based PXE service.
