# ThinClient changelog

## 1.5.1 — 2026-09-16 (prerelease)

- Check installation payloads, tools and configuration before erasing a disk;
  revalidate the target, verify copied boot files, and fail on bootloader,
  configuration or unmount errors.
- Move network changes, Wi-Fi scans, diagnostics, display discovery and installer
  disk scans off the GTK thread; ignore callbacks after a dialog closes.
- Include MMC/eMMC and SDHCI storage drivers in Lite's early boot image, expand
  hardware inventory, and add configurable RAM, NIC and storage test profiles.
- Skip cache-discovery retry sleeps when USB storage is absent and udev is settled;
  retain bounded discovery for late devices and a controller-compatibility override.
- Clear stale build trust keys, signatures and release approvals on rebuild.
- Bound HTTP workers and keep durable status writes off the transfer-progress
  lock, with overload metrics, checksum-verified load tests and VM boot timing.

## 1.5.0 — 2026-09-16

- Reject failed, truncated, interrupted, and unflushed USB-cache writes; verify
  the saved file and retain the previous cache until publication succeeds.
- Separate source tests from tests of the extracted final image. Add CI and
  explicit connection-manager readiness checks for BIOS, UEFI and PXE boots.
- Default new connections to certificate verification and disable Terminal by
  default. Add administrator setup and versioned PBKDF2 password hashes.
- Bound central configuration retrieval, preserve the last valid runtime copy
  during outages, and display configuration status in Help. Support immutable
  HTTPS/required-configuration policy and detached configuration signatures.
- Add signed release manifests, package inventories, local boot/RAM measurements,
  Prometheus server metrics, hardware export, and MAC-targeted canary preparation.
- Separate reusable dialogs and GTK styling from the connection-manager logic.

- Added a built-in PXE HTTP status dashboard, JSON status API, and dedicated
  health endpoint with active-transfer progress and recent client IP/MAC
  inventory.
- Persisted bounded monitor history atomically in a Docker named volume so it
  survives process crashes, container recreation, and host reboots. Transfers
  interrupted by a crash are recovered as failures instead of successful
  boots, and persistence failures now make the container unhealthy.
- Capped each PXE container's Docker logs at three rotating 10 MiB files using
  the efficient `local` driver, preventing unattended access logs from filling
  the host filesystem while retaining normal `docker logs` operation.
- Made human dashboard timestamps follow the deployment's IANA timezone (for
  example, `Asia/Bangkok`) while retaining UTC timestamps in the JSON API.
- Improved the status dashboard with sortable tables, clearer health and
  failure summaries, relative timestamps, transfer progress/throughput,
  responsive layouts, and accessible no-JavaScript controls.

## 1.4.1 — 2026-08-25

- Fixed the configured NTP server queueing behind the built-in
  `pool.ntp.org` default: systemd combines `NTP=` list assignments across
  drop-ins, so the runtime configuration now resets the list first. The
  operator's server (typically the domain controller, which NLA/Kerberos
  depend on) is now actually preferred.
- Comma-separated NTP server lists from the settings UI are normalized to
  the space-separated form timesyncd expects instead of being silently
  discarded.
- Added a time section to the boot diagnostics (`tc-diag`): sync status,
  the NTP servers in use, and recent timesyncd log lines.
- Corrected the UEFI menu wording for Docker deployments: when the
  warm-reboot-safe TFTP kernel/initrd path is selected by default, it is now
  labelled as restart-safe and recommended instead of as a recovery option;
  standalone HTTP-first menus keep their original recommendation.

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
